# app/skills/code/skill.py
"""Code Skill：代码生成，支持快速模式(code_fast)与 Think 模式(code_think)。

本版修复（附件注入断点）
----------------------
RouterParameterProvider 注入的附件键是 `attachments_obj`（= RequestContext.attachments，
对象列表，元素含 .file_id/.name/.type），而旧 CodeSkill 读的是 `kwargs["attachments"]`，
永远拿不到 → 即便路由修对，附件也进不了项目地图。
本版把附件来源改为 `attachments_obj` 优先、兼容 `attachments`，整条 code 链路才真正闭环。

注意：模型分档维持现状（不新增 code 分支）——
- use_think=False → code_fast（deepseek-coder，直接出代码）
- use_think=True  → code_think（deepseek-r1，带思考）
是否需要推理由 _should_think 关键词 + 显式指令 + 长文本判定，不引入第三档。
"""
from __future__ import annotations

import re
from typing import Iterator, List, Optional

from app.contracts.result import Result, text_part
from app.contracts.skill import BaseSkill
from app.core.logger import logger
from app.skills.code.service import code_service


# 自动触发 Think 的关键词（复杂需求）
_THINK_KEYWORDS = (
    "算法", "重构", "排查", "设计", "复杂", "优化", "架构",
    "分析", "调优", "深度", "思考", "逐步", "详细解释", "原理",
    "根因", "定位", "报错", "异常", "bug", "修复",
)

# 强制 Think 的显式指令
_FORCE_THINK_KEYWORDS = (
    "think", "深度思考", "r1", "请思考", "先思考",
)

_CODE_BLOCK_RE = re.compile(
    r"```(?:[a-zA-Z0-9_+\-]*)\n(.*?)```",
    re.DOTALL,
)

# 视作代码/工程文本的扩展名（这些附件会被解析成【项目地图】喂进 code 链路）
_CODE_EXTS = ("txt", "md", "py", "js", "ts", "java", "go", "c", "cpp", "h",
              "json", "yaml", "yml", "sql", "html", "css", "sh", "rs")


class CodeSkill(BaseSkill):
    name = "code_generate"
    description = (
        "根据需求生成或修改代码。适用于：'写一个函数/实现XX/改这段代码/"
        "这段代码报错/帮我排查XX问题/用Python写XX'，以及"
        "'上传代码工程后帮我分析有哪些bug/审查代码/重构'。"
        "复杂逻辑（算法/重构/排查 bug）自动启用 Think 模式（r1 思考过程）。"
        "不适用于：解释代码含义（用 chat）、执行代码（后续 code_execute）。"
    )
    schema = {
        "type": "object",
        "properties": {
            "requirement": {"type": "string", "description": "代码需求描述"},
            "language": {"type": "string", "description": "编程语言，可空"},
            "use_think": {
                "type": "boolean",
                "description": "是否启用 Think 模式，可空（不传则自动判断）",
            },
            "history": {
                "type": "array",
                "description": "历史对话，用于延续上下文和提取代码快照",
            },
        },
        "required": ["requirement"],
    }
    intent = "code_generation"
    category = "general"
    sync = True
    returns = ["text"]
    # 代码任务可带可不带附件：带了源码附件则解析成项目地图；不声明 requires_attachments，
    # 避免 RouterProvider 因缺 file_id 字段而强校验。附件统一从 attachments_obj 取。
    requires_attachments = []
    produces_outputs = False
    visible_to_l2 = True
    visible_to_planner = True

    def execute(
            self,
            requirement: str,
            language: str = "",
            use_think: Optional[bool] = None,
            history: Optional[List[dict]] = None,
            **kwargs,
    ) -> Result:
        if not requirement or not requirement.strip():
            return Result(parts=[text_part("请描述你的代码需求。")])

        history = history or []
        session_id = kwargs.get("session_id") or ""
        attachments = self._get_attachments(kwargs)
        current_code = self._extract_last_code(history)
        think = self._should_think(requirement, use_think)

        project_map = self._load_project_map(attachments, requirement, session_id)
        task_state_text = self._load_task_state(session_id, requirement)

        logger.info(
            f"[CodeSkill] requirement_len={len(requirement)} use_think={think} "
            f"has_snapshot={bool(current_code)} map_len={len(project_map)} "
            f"attachments={len(attachments)} "
            f"history_msgs={len(history)} session={session_id[:8] if session_id else '-'}"
        )

        answer = code_service.generate(
            requirement=requirement, language=language, current_code=current_code,
            use_think=think, history=history,
            project_map=project_map, task_state_text=task_state_text,
        )
        self._update_task_state(session_id, requirement, current_code)
        return Result(parts=[text_part(answer)])

    def stream(
            self,
            requirement: str,
            language: str = "",
            use_think: Optional[bool] = None,
            history: Optional[List[dict]] = None,
            **kwargs,
    ) -> Iterator[str]:
        if not requirement or not requirement.strip():
            yield "请描述你的代码需求。"
            return

        history = history or []
        session_id = kwargs.get("session_id") or ""
        attachments = self._get_attachments(kwargs)
        current_code = self._extract_last_code(history)
        think = self._should_think(requirement, use_think)

        project_map = self._load_project_map(attachments, requirement, session_id)
        task_state_text = self._load_task_state(session_id, requirement)

        logger.info(
            f"[CodeSkill.stream] requirement_len={len(requirement)} use_think={think} "
            f"map_len={len(project_map)} attachments={len(attachments)}"
        )

        yield from code_service.generate_stream(
            requirement=requirement, language=language, current_code=current_code,
            use_think=think, history=history,
            project_map=project_map, task_state_text=task_state_text,
        )
        self._update_task_state(session_id, requirement, current_code)

    # ============================================================
    # 附件获取（兼容两种键 + 两种元素类型：Attachment 对象 / dict）
    # ============================================================
    @staticmethod
    def _get_attachments(kwargs: dict) -> list:
        """RouterProvider 注入的是 attachments_obj（Attachment 对象列表）；
        其它入口可能传 attachments（dict 列表）。两者都兼容。"""
        atts = kwargs.get("attachments_obj")
        if atts is None:
            atts = kwargs.get("attachments")
        return list(atts) if atts else []

    @staticmethod
    def _att_field(att, name: str, default: str = "") -> str:
        if isinstance(att, dict):
            return att.get(name, default) or default
        return getattr(att, name, default) or default

    def _load_project_map(self, attachments, requirement: str, session_id: str) -> str:
        """优先用本轮上传的代码附件生成【项目地图】并缓存；无附件则复用本会话缓存。"""
        from app.skills.code.code_memory import code_memory_service
        from app.config.loader import config

        # 1) 本轮上传了代码文件：解析 → 生成地图 → 缓存
        for att in (attachments or []):
            fid = self._att_field(att, "file_id")
            if not fid:
                continue
            try:
                from app.utils.file_registry import file_registry
                meta = file_registry.get(fid) or {}
                # meta 可能不含 name（dict 附件场景），用附件上的 name 兜底
                path = meta.get("path")
                name = meta.get("name") or self._att_field(att, "name")
                ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                # 附件本身 type=doc 也接受：只要扩展名像代码文本
                if ext not in _CODE_EXTS:
                    continue
                import os
                if not path or not os.path.isfile(path):
                    continue
                with open(path, "rb") as f:
                    raw = f.read()
                text = self._decode_text(raw)
                from app.utils.project_context import build_project_context
                m = build_project_context(
                    text, filename=name, requirement=requirement,
                    project_budget_tokens=int(config.get("code.project_map_max_tokens", 12000)),
                )
                if m:
                    logger.info(
                        f"[CodeSkill] 项目地图已生成并缓存 name={name} "
                        f"map_chars={len(m)} session={session_id[:8] if session_id else '-'}"
                    )
                    code_memory_service.save_project_map(session_id, m)
                    return m
            except Exception as e:
                logger.warning(f"[CodeSkill] 生成项目地图失败（忽略，回退缓存/历史）: {e}")
        # 2) 没上传新代码文件：用本会话缓存的地图（跨轮复用）
        cached = code_memory_service.get_project_map(session_id)
        if cached:
            logger.info(f"[CodeSkill] 复用本会话缓存的项目地图 chars={len(cached)}")
        return cached

    @staticmethod
    def _decode_text(raw: bytes) -> str:
        for enc in ("utf-8", "utf-16", "gb18030"):
            try:
                return raw.decode(enc)
            except Exception:
                continue
        return raw.decode("utf-8", errors="ignore")

    def _load_task_state(self, session_id: str, requirement: str) -> str:
        if not session_id:
            return ""
        try:
            from app.skills.code.code_memory import code_memory_service
            return code_memory_service.render_task_state(
                code_memory_service.get_task_state(session_id))
        except Exception:
            return ""

    def _update_task_state(self, session_id: str, requirement: str, current_code: str) -> None:
        if not session_id:
            return
        try:
            from app.skills.code.code_memory import code_memory_service
            files = re.findall(r"[\w./\-]+\.(?:py|js|ts|java|go|c|cpp|json|md|txt)",
                               requirement or "")
            code_memory_service.update_task_state(
                session_id, goal=requirement[:200],
                touched_files=files[:10] or None,
            )
        except Exception as e:
            logger.debug(f"[CodeSkill] 更新任务态失败（忽略）: {e}")

    # ============================================================
    # 内部：Think 模式判定（code_fast vs code_think 的唯一开关）
    # ============================================================
    @staticmethod
    def _should_think(requirement: str, explicit: Optional[bool]) -> bool:
        if explicit is not None:
            return explicit
        text = (requirement or "").lower()
        if any(kw in text for kw in _FORCE_THINK_KEYWORDS):
            return True
        if any(kw in text for kw in _THINK_KEYWORDS):
            return True
        if len(text) > 300:
            return True
        return False

    # ============================================================
    # 内部：从 history 提取上一次的代码快照
    # ============================================================
    @classmethod
    def _extract_last_code(cls, history: List[dict]) -> str:
        if not history:
            return ""
        for msg in reversed(history):
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content") or ""
            if not isinstance(content, str):
                continue
            stripped = content.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                try:
                    from app.utils.content_parser import parse_content_for_llm
                    content = parse_content_for_llm(content) or content
                except Exception:
                    pass
            blocks = _CODE_BLOCK_RE.findall(content)
            if blocks:
                return blocks[-1].strip()
        return ""


skill = CodeSkill()
