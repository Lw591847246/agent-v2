"""Code Skill：代码生成，支持快速模式和 Think 模式。"""
from __future__ import annotations

import re
from typing import Iterator, List, Optional

from app.contracts.result import Result, text_part
from app.contracts.skill import BaseSkill
from app.core.logger import logger
from app.skills.code.service import code_service


# 触发 Think 模式的关键词（需求较复杂时自动启用）
_THINK_KEYWORDS = (
    "算法", "重构", "排查", "设计", "复杂", "优化", "架构",
    "分析", "调优", "深度", "思考", "逐步", "详细解释", "原理",
)

# 强制 Think 的显式指令
_FORCE_THINK_KEYWORDS = (
    "think", "深度思考", "r1", "请思考", "先思考",
)

# 从 history 里提取代码块的正则
_CODE_BLOCK_RE = re.compile(
    r"```(?:[a-zA-Z0-9_+\-]*)\n(.*?)```",
    re.DOTALL,
)


class CodeSkill(BaseSkill):
    name = "code_generate"
    description = (
        "根据需求生成代码。适用于：'写一个函数/实现XX/改这段代码/用Python写XX'。"
        "复杂逻辑（算法/重构/排查）会自动启用 Think 模式（r1 思考过程）。"
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
                "description": "历史对话，可空，用于提取代码快照",
            },
        },
        "required": ["requirement"],
    }
    intent = "code_generation"
    category = "general"
    sync = True
    returns = ["text"]
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
        current_code = self._extract_last_code(history)
        think = self._should_think(requirement, use_think)

        logger.info(
            f"[CodeSkill] requirement_len={len(requirement)} "
            f"language={language or 'auto'} use_think={think} "
            f"has_snapshot={bool(current_code)} snapshot_len={len(current_code)}"
        )

        answer = code_service.generate(
            requirement=requirement,
            language=language,
            current_code=current_code,
            use_think=think,
        )
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
        current_code = self._extract_last_code(history)
        think = self._should_think(requirement, use_think)

        logger.info(
            f"[CodeSkill.stream] use_think={think} "
            f"has_snapshot={bool(current_code)} snapshot_len={len(current_code)}"
        )

        yield from code_service.generate_stream(
            requirement=requirement,
            language=language,
            current_code=current_code,
            use_think=think,
        )

    # ============================================================
    # 内部：Think 模式判定
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
        """从最近一条 assistant 消息中提取最后一个代码块。"""
        if not history:
            return ""
        for msg in reversed(history):
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content") or ""
            if not isinstance(content, str):
                continue
            blocks = _CODE_BLOCK_RE.findall(content)
            if blocks:
                return blocks[-1].strip()
        return ""


skill = CodeSkill()