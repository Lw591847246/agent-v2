# app/utils/project_context.py
"""把"整库代码"压成一份有界的【项目地图】——不依赖向量库的整库感知。

8GB 显存 + 本地模型 num_ctx 有限，整库 85 万字原文塞不进上下文。但"整库感知"不等于
"把原文全塞进去"：模型真正需要的是结构 + 关键骨架（有哪些文件、每个文件里的
类/函数签名/入口、依赖关系），细节可在多轮里按需追问、或贴具体文件时再展开。

支持两种来源：A) scan_project.py 的 dump（===== 路径 ===== 分隔）；B) 单文件/普通文本。
纯文本骨架抽取，无第三方 AST 依赖、零安装。
"""
from __future__ import annotations

import os
import re
from typing import List, Dict

from app.core.logger import logger
from app.utils.context_budget import estimate_tokens, fit

_DUMP_SPLIT = re.compile(r"(?m)^=====\s*(.+?)\s*=====\s*$")

_SKELETON_PATTERNS = [
    re.compile(r"^\s*(import|from)\s+[\w\.]+\b"),
    re.compile(r"^\s*(async\s+def|def|class)\s+\w+"),
    re.compile(r"^\s*@\w+"),
    re.compile(r"^\s*(app|router|api)\.(get|post|put|delete|include_router)\b"),
    re.compile(r"^[A-Z][A-Z0-9_]{2,}\s*="),
    re.compile(r"^\s*raise\s+\w*Error\b"),
    re.compile(r"^(if __name__|def main|async def main)\b"),
]

_NOISE = re.compile(r'^\s*(#|//|--|"""|\'\'\'|\*|/\*)')


def _is_skeleton(line: str) -> bool:
    stripped = line.rstrip("\n")
    if not stripped.strip():
        return False
    if _NOISE.match(stripped) and ":" not in stripped:
        return False
    return any(p.match(stripped) for p in _SKELETON_PATTERNS)


def _split_dump(text: str) -> List[Dict[str, str]]:
    matches = list(_DUMP_SPLIT.finditer(text))
    if not matches:
        return [{"path": "single_file", "body": text}]
    files: List[Dict[str, str]] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        files.append({"path": m.group(1).strip(), "body": text[start:end]})
    return files


def _score(path: str, body: str, keywords: List[str]) -> int:
    score = 0
    low_p = path.lower()
    low_b = body.lower()
    for kw in keywords:
        k = kw.lower().strip()
        if not k or len(k) < 2:
            continue
        if k in low_p:
            score += 5
        if k in low_b:
            score += 2
    base = os.path.basename(low_p)
    if base in ("main.py", "app.py", "bootstrap.py", "__init__.py"):
        score += 3
    if "service" in low_p or "skill" in low_p or "router" in low_p:
        score += 1
    return score


def _skeleton_of(body: str, max_lines: int = 60) -> str:
    out: List[str] = []
    total = body.count("\n") + 1
    kept = 0
    for ln in body.splitlines():
        if _is_skeleton(ln):
            out.append(ln.strip())
            kept += 1
            if kept >= max_lines:
                out.append(f"    ...[本文件骨架行已达上限 {max_lines}，共约 {total} 行]")
                break
    return "\n".join(out) if out else "    (无显著骨架行，可能为纯数据/配置文件)"


def build_project_context(content: str, filename: str = "",
                          requirement: str = "",
                          project_budget_tokens: int = 22000,
                          chars_per_token: float = 1.7) -> str:
    """产出【项目地图】文本，长度受 project_budget_tokens 控制。"""
    if not content or not content.strip():
        return ""

    keywords = []
    for kw in re.split(r"[，,。.、/（）()：:\s]+", requirement or ""):
        kw = kw.strip()
        if len(kw) >= 2:
            keywords.append(kw)

    files = _split_dump(content)
    scored = sorted(
        ((f, _score(f["path"], f["body"], keywords)) for f in files),
        key=lambda x: x[1], reverse=True,
    )

    tree_lines = [f"- {f['path']}（约 {f['body'].count(chr(10)) + 1} 行）"
                  for f, _ in scored]
    tree = "\n".join(tree_lines[:200])
    head = (
        f"【项目地图·整库结构摘要】来源文件：{filename or '未知'}｜共 {len(files)} 个文件\n"
        f"说明：以下为整库的**结构骨架**（文件清单 + 各文件的 import/函数/类/路由签名），"
        f"不是全文。需要看某个文件的具体实现请在追问中点名该文件。\n\n"
        f"== 文件清单 ==\n{tree}\n"
    )
    head_tokens = estimate_tokens(head, chars_per_token)

    body_budget = max(1000, project_budget_tokens - head_tokens)
    n = len(scored)
    per_file_tokens = max(120, body_budget // max(1, min(n, 40)))
    max_lines_per_file = max(15, int(per_file_tokens / 6))

    sections: List[str] = []
    used = 0
    for f, sc in scored:
        if used >= body_budget:
            sections.append("\n== [其余较低相关文件已省略，可点名追问] ==")
            break
        skel = _skeleton_of(f["body"], max_lines=max_lines_per_file)
        block = f"\n\n<<<<< 文件：{f['path']}（相关度 {sc}）>>>>>\n{skel}"
        block_tokens = estimate_tokens(block, chars_per_token)
        if block_tokens > per_file_tokens * 3:
            block = fit(block, per_file_tokens * 3, chars_per_token, keep="head")
            block_tokens = estimate_tokens(block, chars_per_token)
        sections.append(block)
        used += block_tokens

    result = head + "".join(sections)
    if estimate_tokens(result, chars_per_token) > project_budget_tokens:
        result = fit(result, project_budget_tokens, chars_per_token, keep="head")
        result += "\n\n[项目地图按 num_ctx 预算已截断；更早/较低相关文件请点名索取]"

    logger.info(
        f"[ProjectContext] files={len(files)} map_chars={len(result)} "
        f"~tokens={estimate_tokens(result, chars_per_token)} budget={project_budget_tokens}"
    )
    return result
