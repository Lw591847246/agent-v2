# app/application/chat_service.py
"""对话主流程：context → scheduler → executor → orchestrator。"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Union

from app.application.conversation_service import conversation_service
from app.context.context_provider import context_provider
from app.context.conversation_orchestrator import conversation_orchestrator
from app.contracts.context import RequestContext
from app.contracts.result import Result, StreamResult, error_part
from app.contracts.scheduler import RoutingDecision
from app.core.gpu_lock import GPUResource, gpu_lock
from app.core.logger import logger
from app.core.token_recorder import token_recorder
from app.core.trace_context import get_trace_id, set_trace_context
from app.executor.executor import executor
from app.scheduler.router_scheduler import router_scheduler


@dataclass
class ChatHandleResult:
    result: Union[Result, StreamResult]
    is_stream: bool
    ctx: RequestContext
    decision: Optional[RoutingDecision] = None
    session_id: str = ""


class ChatService:

    def __init__(self, scheduler=None):
        self.scheduler = scheduler or router_scheduler

    def handle(
        self,
        text: str,
        history: List[Dict],
        user_id: int,
        session_id: str,
        attachments: List[Dict] = None,
        flow_context: Optional[Dict] = None,
        want_stream: bool = False,
        client_id: str = "",
    ) -> ChatHandleResult:
        # 如果 controller 已经 set 了 trace_id，这里复用；否则生成新的
        current_trace = get_trace_id()
        set_trace_context(trace_id=current_trace, session_id=session_id, user_id=user_id)
        start_ms = int(time.time() * 1000)

        # ⭐ 嵌套保护：controller 已持锁时不会重复抢；独立调用（测试/脚本）时兜底
        with gpu_lock.lease(GPUResource.OLLAMA_CHAT, task="chat-service", timeout=600):
            # 1. 保存用户消息（client_id 幂等）
            if session_id and (
                not client_id
                or not conversation_service.exists_user_message_by_client_id(session_id, client_id)
            ):
                try:
                    conversation_service.save_message(
                        session_id, "user", text, client_id=client_id or None,
                    )
                except Exception as e:
                    logger.warning(f"[ChatService] 保存用户消息失败: {e}")

            # 2. 首条生成标题
            if session_id and conversation_service.message_count(session_id) <= 1:
                try:
                    conversation_service.generate_title(session_id, text)
                except Exception:
                    pass

            # 3. 构建上下文
            ctx = context_provider.build(
                session_id=session_id,
                text=text,
                user_id=user_id,
                raw_attachments=attachments or [],
                flow_context=flow_context,
                want_stream=want_stream,
            )

            # 4. 调度（内部 LLM 调用会嵌套 lease，不会重复抢锁）
            decision = self.scheduler.route(ctx)

            # 5. 执行
            if decision.skill:
                result = executor.run(decision.skill, decision.kwargs, ctx)
            else:
                result = Result(parts=[error_part("无法识别意图")])

        ctx.params["_duration_ms"] = int(time.time() * 1000) - start_ms
        is_stream = isinstance(result, StreamResult)
        return ChatHandleResult(
            result=result,
            is_stream=is_stream,
            ctx=ctx,
            decision=decision,
            session_id=session_id,
        )

    def finalize(
        self,
        hr: ChatHandleResult,
        parts: List[dict],
        outputs: Optional[List[dict]] = None,
        full_text: str = "",
    ) -> int:
        """落库 assistant 消息 + 触发 orchestrator。返回 msg_id。"""
        duration_ms = hr.ctx.params.get("_duration_ms", 0)
        token_stats = token_recorder.pop_trace_stats(get_trace_id())
        msg_id = 0
        try:
            content = full_text if full_text else self._parts_to_content(parts)
            message_type = self._infer_type(parts)
            msg_id = conversation_service.save_assistant_message(
                session_id=hr.session_id,
                content=content,
                message_type=message_type,
                outputs=outputs,
                duration_ms=duration_ms,
                input_tokens=token_stats["input_tokens"],
                output_tokens=token_stats["output_tokens"],
                total_cost=token_stats["cost"],
            )
            logger.info(f"[ChatService] 落库 msg_id={msg_id} type={message_type}")
        except Exception as e:
            logger.warning(f"[ChatService] 落库失败: {e}")

        try:
            conversation_orchestrator.after_turn(
                hr.ctx,
                hr.decision,
                hr.decision.skill.name if hr.decision and hr.decision.skill else "",
            )
        except Exception as e:
            logger.warning(f"[ChatService] orchestrator 失败: {e}")

        return msg_id

    @staticmethod
    def _parts_to_content(parts: List[dict]) -> str:
        return json.dumps(parts, ensure_ascii=False)

    @staticmethod
    def _infer_type(parts: List[dict]) -> str:
        types = {p.get("type") for p in parts}
        if "video" in types:
            return "video"
        if "image" in types:
            return "image"
        if "audio" in types:
            return "audio"
        if "card" in types:
            return "card"
        if len(parts) > 1:
            return "multi"
        return "text"


chat_service = ChatService()