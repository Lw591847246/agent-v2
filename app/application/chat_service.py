"""对话主流程：context -> scheduler -> executor -> orchestrator。"""
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
from app.infrastructure.repositories.task_repo import task_repo
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

        # 嵌套保护：controller 已持锁时不会重复抢；独立调用（测试/脚本）时兜底
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
        """落库 assistant 消息 + 触发 orchestrator。返回 msg_id。

        完整版修复要点：
        1. 失败统一归一化逻辑保留，_is_failure / _build_failure_parts 已在类内真正实现，
           不再因调用未定义方法抛 AttributeError；
        2. token 统计取值全部走安全兜底，跨线程 contextvar 丢失（get_trace_id 返回 None、
           pop_trace_stats 返回 None 或缺键）时不再抛 NoneType 下标异常；
        3. 落库异常从 warning 升级为 error + exc_info，不再静默吞栈。
        """
        duration_ms = hr.ctx.params.get("_duration_ms", 0)
        msg_id = 0

        try:
            # 修复1：token 统计安全获取（跨线程时 get_trace_id() 可能为 None）
            token_stats = token_recorder.pop_trace_stats(get_trace_id()) or {}
            input_tokens = token_stats.get("input_tokens", 0) or 0
            output_tokens = token_stats.get("output_tokens", 0) or 0
            total_cost = token_stats.get("cost", 0.0) or 0.0

            # 修复2：失败统一归一化（方法已实现进类内，调用安全）
            if self._is_failure(parts):
                failure_parts = self._build_failure_parts(parts)
                content = json.dumps(failure_parts, ensure_ascii=False)
                message_type = "multi"
                outputs = []
                logger.info("[ChatService] 检测到失败，归一化落库")
            else:
                content = full_text if full_text else self._parts_to_content(parts)
                message_type = self._infer_type(parts)

            msg_id = conversation_service.save_assistant_message(
                session_id=hr.session_id,
                content=content,
                message_type=message_type,
                outputs=outputs,
                duration_ms=duration_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_cost=total_cost,
            )
            logger.info(f"[ChatService] 落库 msg_id={msg_id} type={message_type}")

            # 根治：把占位消息 id 显式回写到 task 行，建立 task -> msg 显式外键，
            # 供后台 _run_async / task_runner.finish 精确定位回填，不再依赖 content LIKE。
            if msg_id and hr.result is not None and getattr(hr.result, "task_id", None):
                try:
                    task_repo.set_msg_id(hr.result.task_id, msg_id)
                except Exception as e:
                    logger.warning(f"[ChatService] 回写 task.msg_id 失败: {e}")
        except Exception as e:
            # 修复3：不再静默吞异常，error + 堆栈
            logger.error(f"[ChatService] 落库失败: {e}", exc_info=True)

        try:
            conversation_orchestrator.after_turn(
                hr.ctx,
                hr.decision,
                hr.decision.skill.name if hr.decision and hr.decision.skill else "",
            )
        except Exception as e:
            logger.warning(f"[ChatService] orchestrator 失败: {e}")

        return msg_id

    # ============================================================
    # 失败判定与归一化（完整版实现）
    # 真实契约：app/contracts/result.py 中
    #   error_part(message) -> {"type": "text", "content": "❌ <message>"}
    # 因此失败的确定性标记 = text part 且 content 以 "❌" 开头；
    # 同时兼容 type == "error" 与显式 is_error / error 字段；
    # 文本前缀兜底仅在"单个 text part"时启用，避免误伤多 part 正文里偶现的"失败"二字。
    # ============================================================
    _ERR_PREFIX = "\u274c"  # 失败标记字符，与 error_part 产出一致
    _FAILURE_TEXT_PREFIXES = ("任务失败", "生成失败", "执行失败", "请求失败", "处理失败")

    @classmethod
    def _extract_error_message(cls, parts: List[dict]) -> Optional[str]:
        """从 parts 中提取错误信息；非失败返回 None。"""
        if not parts:
            return None

        # 结构化标记优先：error_part 的 ❌ 前缀 / type == error / 显式错误字段
        for p in parts:
            if not isinstance(p, dict):
                continue
            ptype = p.get("type")
            if ptype == "error":
                return str(p.get("error") or p.get("content") or p.get("message") or "未知错误").strip()
            if p.get("is_error") or p.get("isError") or p.get("error"):
                return str(p.get("error") or p.get("content") or "未知错误").strip()
            if ptype == "text":
                c = str(p.get("content", "")).strip()
                if c.startswith(cls._ERR_PREFIX):
                    return c[len(cls._ERR_PREFIX):].strip() or "未知错误"

        # 文本前缀兜底：仅当只有一个 text part 时启用，避免误伤多 part 正文
        text_parts = [p for p in parts if isinstance(p, dict) and p.get("type") == "text"]
        if len(parts) == 1 and len(text_parts) == 1:
            c = str(text_parts[0].get("content", "")).strip()
            for pref in cls._FAILURE_TEXT_PREFIXES:
                if c.startswith(pref):
                    return c
        return None

    @classmethod
    def _is_failure(cls, parts: List[dict]) -> bool:
        """判定一组 parts 是否为失败结果。"""
        return cls._extract_error_message(parts) is not None

    @classmethod
    def _build_failure_parts(cls, parts: List[dict]) -> List[dict]:
        """归一化成标准失败格式：[{"type":"text","content":"任务失败：<msg>"}]。"""
        msg = cls._extract_error_message(parts) or "任务执行失败"
        # 去掉可能残留的 ❌ 与"任务失败："前缀，避免叠加
        if msg.startswith(cls._ERR_PREFIX):
            msg = msg[len(cls._ERR_PREFIX):].strip()
        if not msg.startswith("任务失败"):
            msg = f"任务失败：{msg}"
        return [{"type": "text", "content": msg}]

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
