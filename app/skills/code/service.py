# app/skills/code/service.py
"""Code 业务：把"项目地图 + 模块地图 + 任务态 + 近窗对话 + 相关代码切片 + 本轮需求"
按 token 预算装配后交给模型。

本版改动（接入 code workspace）
--------------------------------
- 新增可选参数 user_id / session_id / scope / module_id：传了就自动从
  app.skills.code.workspace 拉取项目地图、模块地图、固定约束、历史摘要、
  近窗对话、相关代码切片；
- 原有 project_map / task_state_text / history 参数仍可用，且优先级更高
  （外部显式传入覆盖 workspace 自动取值），向后兼容 skill.py 旧调用；
- 每段独立 token 预算，全部走 app.utils.context_budget.fit；
- 装配顺序：system → 项目地图 → 模块地图 → 任务态 → 近窗历史 → 相关切片 → 本轮需求。
"""
from __future__ import annotations

import re
from typing import Iterator, List, Optional

from app.config.loader import config
from app.core.logger import logger
from app.infrastructure.clients.ollama_client import ollama_client
from app.utils.context_budget import estimate_tokens, fit, fit_messages
from app.skills.code.prompts import (
    SYSTEM_PROMPT_FAST,
    SYSTEM_PROMPT_THINK_GENERATE,
    SYSTEM_PROMPT_THINK_FIX,
)

try:
    from app.skills.code.workspace.context.builder import build_code_context
    from app.skills.code.workspace.contracts import ContextPack
except Exception as _e:  # workspace 未安装/未初始化时降级，不影响旧调用
    build_code_context = None  # type: ignore
    ContextPack = object  # type: ignore
    logger.warning(f"[CodeService] workspace 上下文不可用，降级为旧行为: {_e}")


_FIX_KEYWORDS = (
    "报错", "异常", "bug", "修复", "排查", "定位", "根因",
    "不工作", "失败", "错误", "为什么", "怎么解决", "怎么修",
    "crash", "error", "exception", "traceback", "stack trace",
)


class CodeService:

    # ---------------- token 预算 ----------------
    def _history_budget(self) -> int:
        return int(config.get("code.history_max_tokens", 6000) or 6000)

    def _map_budget(self) -> int:
        return int(config.get("code.project_map_max_tokens", 12000) or 12000)

    def _module_map_budget(self) -> int:
        return int(config.get("code.module_map_max_tokens", 6000) or 6000)

    def _task_budget(self) -> int:
        return int(config.get("code.task_state_max_tokens", 600) or 600)

    def _slices_budget(self) -> int:
        return int(config.get("code.code_slices_max_tokens", 6000) or 6000)

    # ---------------- 对外入口 ----------------


    def generate_stream(
        self, requirement: str, language: str = "", current_code: str = "",
        use_think: bool = False, history: Optional[List[dict]] = None,
        project_map: str = "", task_state_text: str = "",
        user_id: int = 0, session_id: str = "", scope: str = "all",
        module_id: str = "",
    ) -> Iterator[str]:
        model_key = "code_think" if use_think else "code_fast"
        task_type = self._detect_task_type(requirement, current_code)
        ctx_pack = self._resolve_context(user_id, session_id, requirement, scope, module_id)
        messages = self._build_messages(
            requirement, language, current_code, use_think, history, task_type,
            project_map, task_state_text, ctx_pack=ctx_pack,
        )
        logger.info(
            f"[CodeService.stream] model={model_key} task={task_type} "
            f"history_msgs={len(history or [])} map_chars={len(project_map)} "
            f"ctx={'yes' if ctx_pack else 'no'}"
        )
        try:
            yield from ollama_client.stream(service="chat", model_key=model_key, messages=messages)
        except Exception as e:
            logger.error(f"[CodeService] stream 失败 model={model_key}: {e}")
            yield f"代码生成失败：{e}"

    # ---------------- 工作区上下文 ----------------
    def _resolve_context(
        self, user_id: int, session_id: str, requirement: str,
        scope: str, module_id: str,
    ) -> Optional["ContextPack"]:
        """有 user/session 才去拉工作区上下文；失败降级为空，不影响主流程。"""
        if not build_code_context or not user_id or not session_id:
            return None
        try:
            return build_code_context(
                uid=int(user_id), sid=str(session_id),
                user_text=requirement or "",
                scope=scope or "all", module_id=module_id or "",
            )
        except Exception as e:
            logger.warning(f"[CodeService] 构建工作区上下文失败（降级为旧行为）: {e}")
            return None

    @staticmethod
    def _detect_task_type(requirement: str, current_code: str) -> str:
        text_lower = (requirement or "").lower()
        if any(kw in text_lower for kw in _FIX_KEYWORDS):
            return "fix"
        if current_code and len(current_code) > 100:
            if "重写" not in text_lower and "从零" not in text_lower:
                return "fix"
        return "generate"

    # ---------------- messages 装配 ----------------
    def _build_messages(
        self, requirement: str, language: str = "", current_code: str = "",
        use_think: bool = False, history: Optional[List[dict]] = None,
        task_type: str = "generate", project_map: str = "",
        task_state_text: str = "", ctx_pack: Optional["ContextPack"] = None,
    ) -> list:
        if use_think:
            system_prompt = (SYSTEM_PROMPT_THINK_FIX if task_type == "fix"
                             else SYSTEM_PROMPT_THINK_GENERATE)
        else:
            system_prompt = SYSTEM_PROMPT_FAST

        messages: List[dict] = [{"role": "system", "content": system_prompt}]

        # 1) 项目地图：外部显式传入优先；否则用 workspace 项目地图
        effective_project_map = project_map or (getattr(ctx_pack, "code_map", "") if ctx_pack else "")
        if effective_project_map:
            pm = fit(effective_project_map, self._map_budget(), keep="head_tail")
            messages.append({
                "role": "system",
                "content": "【项目全库地图（结构骨架，非全文）——定位代码时优先参考，"
                           "需要某文件完整实现时在回答里说明需我补充该文件】\n" + pm,
            })

        # 2) 模块地图：用户指定了 module_id 才会有
        module_map = getattr(ctx_pack, "module_map", "") if ctx_pack else ""
        if module_map:
            mm = fit(module_map, self._module_map_budget(), keep="head_tail")
            messages.append({
                "role": "system",
                "content": "【当前模块地图（本次改动范围）】\n" + mm,
            })

        # 3) 任务态：外部显式传入优先；否则 pins + 滚动摘要
        effective_task_state = task_state_text
        if not effective_task_state and ctx_pack:
            pins = getattr(ctx_pack, "pins", []) or []
            summary = getattr(ctx_pack, "summary", "") or ""
            buf = []
            if pins:
                buf.append("【固定约束】\n" + "\n".join(f"- {p}" for p in pins))
            if summary:
                buf.append("【历史摘要】\n" + summary)
            effective_task_state = "\n\n".join(buf)
        if effective_task_state:
            messages.append({
                "role": "system",
                "content": fit(effective_task_state, self._task_budget(), keep="head"),
            })

        # 4) 近窗历史：外部 history 优先；否则用 workspace 的 recent_turns
        hist_source = history or (getattr(ctx_pack, "recent_turns", []) if ctx_pack else [])
        hist_msgs = self._build_history_messages(hist_source or [])
        hist_msgs = fit_messages(hist_msgs, self._history_budget(), protect_roles=())
        messages.extend(hist_msgs)

        # 5) 相关代码切片：workspace 按关键词挑出的少量切片
        chunks = getattr(ctx_pack, "relevant_chunks", []) if ctx_pack else []
        if chunks:
            rendered = self._render_chunks(chunks)
            if rendered:
                messages.append({
                    "role": "system",
                    "content": "【相关代码切片（按本轮需求关键词召回，引用时写清文件与行号）】\n"
                               + fit(rendered, self._slices_budget(), keep="head_tail"),
                })

        # 6) 本轮需求（含工作区快照）
        parts = []
        if language:
            parts.append(f"请用 {language} 实现：")
        parts.append(requirement)
        if current_code:
            parts.append(f"\n\n【当前工作区的代码版本如下，请在此基础上修改】：\n```\n{current_code}\n```")
        messages.append({"role": "user", "content": "\n".join(parts)})
        return messages

    @staticmethod
    def _render_chunks(chunks: List[dict]) -> str:
        lines = []
        for c in chunks:
            file = c.get("file", "")
            lines_no = c.get("lines", "")
            content = c.get("content", "")
            if not file or not content:
                continue
            lines.append(f"--- {file} ({lines_no}) ---\n{content}")
        return "\n\n".join(lines)

    def _build_history_messages(self, history: List[dict]) -> list:
        if not history:
            return []
        recent = history[-(int(config.get("code.history_max_turns", 40)) * 2):]
        result: list = []
        for msg in recent:
            role = msg.get("role")
            if role not in ("user", "assistant"):
                continue
            content = msg.get("content") or ""
            if not isinstance(content, str) or not content.strip():
                continue
            content = self._normalize_content(content)
            if role == "assistant":
                content = self._truncate_code_blocks(content)
            result.append({"role": role, "content": content})
        return result

    @staticmethod
    def _normalize_content(content: str) -> str:
        if not content:
            return ""
        stripped = content.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                from app.utils.content_parser import parse_content_for_llm
                return parse_content_for_llm(content) or content
            except Exception:
                return content
        return content

    @staticmethod
    def _truncate_code_blocks(content: str, max_block: int = 4000) -> str:
        code_re = re.compile(r"```(?:[a-zA-Z0-9_+\-]*)\n(.*?)```", re.DOTALL)

        def _shrink(m):
            inner = m.group(1)
            if len(inner) <= max_block:
                return m.group(0)
            head = inner[: int(max_block * 0.6)]
            tail = inner[-int(max_block * 0.4):]
            return "```\n" + head + "\n...[历史代码块已压缩]...\n" + tail + "\n```"

        return code_re.sub(_shrink, content)


code_service = CodeService()