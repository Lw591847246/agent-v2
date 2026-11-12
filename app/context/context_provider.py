# app/context/context_provider.py
"""上下文构建：window/summary/recent_intents + 附件解析 + 引用注入。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.application.conversation_service import conversation_service
from app.context.attachment_resolver import attachment_resolver
from app.context.reference_resolver import reference_resolver
from app.contracts.context import RequestContext
from app.core.logger import logger
from app.models.attachment import Attachment


class ContextProvider:
    def build(
        self,
        session_id: str,
        text: str,
        user_id: int = 0,
        raw_attachments: Optional[List[dict]] = None,
        flow_context: Optional[Dict[str, Any]] = None,
        want_stream: bool = False,
    ) -> RequestContext:
        # 1. 拿上下文
        context = {"summary": "", "window": [], "recent_intents": []}
        try:
            context = conversation_service.get_context(session_id, current_text=text)
        except Exception as e:
            logger.warning(f"[ContextProvider] get_context failed: {e}")

        # 2. 解析附件
        attachments = attachment_resolver.resolve_many(raw_attachments or [])

        # 3. 引用解析（没附件时才尝试注入）
        if not attachments and text:
            try:
                injected = reference_resolver.resolve(
                    text, history=context.get("window", []), default_kind="image",
                )
                if injected:
                    attachments = injected
                    logger.info(f"[ContextProvider] 引用注入 {len(injected)} 个附件")
            except Exception as e:
                logger.warning(f"[ContextProvider] 引用解析失败: {e}")

        # 4. 组装
        return RequestContext(
            text=text,
            user_id=user_id,
            session_id=session_id,
            attachments=attachments,
            history=context.get("window", []),
            full_history=context.get("window", []),
            summary=context.get("summary", ""),
            recent_intents=context.get("recent_intents", []),
            flow_context=flow_context,
            want_stream=want_stream,
        )


context_provider = ContextProvider()