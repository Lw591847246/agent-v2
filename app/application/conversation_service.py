"""会话服务。"""
import re
import threading
import uuid
from typing import Any, Dict, List, Optional
import contextvars

from app.core.exceptions import NotFoundException
from app.core.gpu_lock import GPUResource, gpu_lock
from app.core.logger import logger
from app.domain.repositories.conversation_repository import ConversationRepository
from app.infrastructure.clients.ollama_client import ollama_client
from app.infrastructure.repositories.conversation_repo import conversation_repo
from app.utils.content_parser import parse_content_for_llm
from app.core.llm_tracker import track_llm

_summary_locks: Dict[str, threading.Lock] = {}

_SUMMARY_PROMPT_TMPL = """你是对话历史摘要助手。请将以下对话压缩成简洁摘要。

【已有摘要】
{existing_summary}

【新增对话】
{new_dialogue}

【要求】
1. 输出一段 {max_chars} 字以内的摘要，**接续**已有摘要（不要重复已有内容）
2. 必须保留：
   - 用户的核心意图（每轮要做什么）
   - 生成的产物（图片/文档/代码/视频的文件名或内容概要）
   - 用户表达的偏好/约束
   - 关键事实、数字、名称、角色、情节设定
   - 未完成的任务或待续内容
3. 可以丢弃：寒暄、重复内容、已完成的中性内容
4. 直接输出摘要文本，不要前后缀、不要解释
"""


class ConversationService:

    def __init__(self):
        self._repo: ConversationRepository = conversation_repo

    # ============================================================
    # 会话
    # ============================================================

    def ensure_conversation(self, session_id: str, user_id: str):
        if not self._repo.exists(session_id):
            self._repo.create(session_id, user_id)

    def exists_user_message_by_client_id(self, session_id: str, client_id: str) -> bool:
        return self._repo.exists_user_message_by_client_id(session_id, client_id)

    def new_conversation(self, user_id: str) -> str:
        session_id = uuid.uuid4().hex
        self._repo.create(session_id, user_id)
        return session_id

    def list(self, user_id: str) -> List[dict]:
        return self._repo.list_by_user(user_id)

    def messages(self, session_id: str, user_id: str = None) -> dict:
        if not self._repo.exists(session_id):
            raise NotFoundException("会话不存在")
        return {
            "session_id": session_id,
            "messages": self._repo.get_all_messages(session_id, user_id),
        }

    def save_message(
        self, session_id: str, role: str, content: str,
        message_type: str = "text",
        image_filename: str = None, thumb_filename: str = None,
        client_id: str = None, duration_ms: int = None,
    ):
        return self._repo.save_message(
            session_id=session_id, role=role, content=content,
            message_type=message_type,
            image_filename=image_filename, thumb_filename=thumb_filename,
            client_id=client_id, duration_ms=duration_ms,
        )

    def save_image_message(
        self, session_id: str, role: str, content: str,
        image_filename: str, thumb_filename: str, duration_ms: int = None,
    ):
        return self.save_message(
            session_id=session_id, role=role, content=content,
            message_type="image",
            image_filename=image_filename, thumb_filename=thumb_filename,
            duration_ms=duration_ms,
        )

    def get_history(self, session_id: str, user_id: str = None) -> List[dict]:
        raw_history = self._repo.get_messages(session_id)
        result = []
        for m in raw_history:
            content = parse_content_for_llm(m.get("content", ""))
            if not content:
                continue
            result.append({"role": m.get("role", "user"), "content": content})
        return result

    def message_count(self, session_id: str) -> int:
        return self._repo.message_count(session_id)

    # ============================================================
    # 标题
    # ============================================================

    def generate_title(self, session_id: str, text: str):
        title = self._rule_based_title(text)
        self._repo.update_title(session_id, title)
        logger.info(f"【标题】本地规则生成: {title}")

        ctx = contextvars.copy_context()
        threading.Thread(
            target=ctx.run, args=(self._async_optimize_title, session_id, text),
            daemon=True,
        ).start()

    def _async_optimize_title(self, session_id: str, text: str):
        try:
            prompt = (
                f"请为以下对话生成一个不超过 12 个字的简短标题，"
                f"只输出标题文字，不要标点、不要引号、不要解释：\n\n{text}"
            )
            # ⭐ 标题优化是独立后台线程，独立抢锁；短任务超时 60s
            with gpu_lock.lease(GPUResource.OLLAMA_CHAT, task="title-opt", timeout=60):
                result = ollama_client.chat(
                    service="intent", model_key="intent",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                )
            new_title = result.strip().strip('"').strip("'").strip("。").strip()
            if 3 <= len(new_title) <= 20:
                self._repo.update_title(session_id, new_title)
                logger.info(f"【标题】llama 优化成功: {new_title}")
            else:
                logger.warning(f"【标题】llama 输出异常，保留本地版本: {new_title}")
        except Exception as e:
            logger.warning(f"【标题】llama 优化失败，保留本地版本: {e}")

    def _rule_based_title(self, text: str) -> str:
        if not text or not text.strip():
            return "新对话"
        title = re.sub(r"\s+", " ", text.strip())
        prefixes = ["帮我", "帮我生成", "请帮我", "请", "能否帮我", "可以帮我", "麻烦帮我"]
        for p in prefixes:
            if title.startswith(p):
                title = title[len(p):].strip()
                break
        if len(title) > 20:
            title = title[:20] + "..."
        return title or "新对话"

    # ============================================================
    # assistant 消息（带 outputs）
    # ============================================================

    def save_assistant_message(
        self, session_id: str, content: str,
        message_type: str = "text",
        outputs: Optional[List[Dict[str, Any]]] = None,
        image_filename: Optional[str] = None,
        thumb_filename: Optional[str] = None,
        duration_ms: Optional[int] = None,
        project_id: Optional[int] = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_cost: float = 0.0
    ) -> int:
        return self._repo.save_assistant_message(
            session_id=session_id, content=content, message_type=message_type,
            outputs=outputs, image_filename=image_filename,
            thumb_filename=thumb_filename, duration_ms=duration_ms,
            project_id=project_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_cost=total_cost,
        )

    def get_recent_outputs(
        self, session_id: str, limit: int = 20,
        before_message_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        return self._repo.get_recent_outputs(
            session_id=session_id, limit=limit,
            before_message_id=before_message_id,
        )

    def find_referenced_outputs(
        self, session_id: str, referent: Any, max_lookback: int = 20,
    ) -> List[Dict[str, Any]]:
        """解析引用 + 挑产出。V2 收紧：用 pick_outputs_strict（返回 dict）。"""
        from app.context.reference_resolver import pick_outputs_strict

        history = self.get_recent_outputs(session_id, limit=max_lookback)
        if not history:
            logger.info(f"[Referent] session={session_id} 无任何产出")
            return []

        result = pick_outputs_strict(history, referent)
        if result["reason"] != "ok":
            return []
        picked = result["outputs"]

        results: List[Dict[str, Any]] = []
        for p in picked:
            matched_mid = None
            for h in history:
                for o in h.get("outputs", []):
                    if o.get("filename") == p.get("filename") and o.get("kind") == p.get("kind"):
                        matched_mid = h.get("message_id")
                        break
                if matched_mid is not None:
                    break
            merged = dict(p)
            merged["message_id"] = matched_mid
            results.append(merged)
        return results

    def get_recent_messages(
        self, session_id: str, limit: int = 20,
        before_message_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        return self._repo.get_recent_messages(
            session_id=session_id, limit=limit,
            before_message_id=before_message_id,
        )

    # ============================================================
    # 上下文
    # ============================================================

    def get_context(self, session_id: str, current_text: str = "") -> Dict[str, Any]:
        from app.config.loader import config

        ctx_cfg = (config.get("agent.context") or {})
        window_size = int(ctx_cfg.get("window_size", 10))

        latest_id = self._repo.get_max_message_id(session_id)
        latest_summary = self._repo.get_latest_summary(session_id)
        summary_text = latest_summary["summary"] if latest_summary else ""
        summary_upto = latest_summary["upto_message_id"] if latest_summary else 0

        window = self._repo.get_recent_messages(
            session_id, limit=window_size + 1, include_outputs=True,
        )

        if window and window[-1].get("role") == "user" and current_text:
            last_content = (window[-1].get("content") or "").strip()
            if last_content == current_text.strip():
                window = window[:-1]

        if len(window) > window_size:
            window = window[-window_size:]

        recent_intents = self._repo.get_recent_intents(session_id)

        return {
            "summary": summary_text,
            "window": window,
            "recent_intents": recent_intents,
            "summary_upto_id": summary_upto,
            "latest_id": latest_id,
        }

    def maybe_generate_summary(self, session_id: str) -> None:
        from app.config.loader import config
        if not config.get("agent.context.enabled", True):
            return
        if not config.get("agent.context.summary_async", True):
            self._do_generate_summary(session_id)
            return

        lock = _summary_locks.setdefault(session_id, threading.Lock())
        if not lock.acquire(blocking=False):
            return

        def _run():
            try:
                self._do_generate_summary(session_id)
            finally:
                lock.release()

        threading.Thread(target=_run, daemon=True, name=f"sum-{session_id[:8]}").start()

    def _do_generate_summary(self, session_id: str) -> None:
        from app.config.loader import config
        from app.infrastructure.clients.ollama_client import ollama_client
        from app.core.logger import logger

        ctx_cfg = (config.get("agent.context") or {})
        window_size = int(ctx_cfg.get("window_size", 10))
        trigger = int(ctx_cfg.get("summary_trigger_count", 6))
        max_chars = int(ctx_cfg.get("summary_max_chars", 800))
        svc = ctx_cfg.get("summary_service", "chat")
        mkey = ctx_cfg.get("summary_model_key", "chat")

        latest_id = self._repo.get_max_message_id(session_id)
        latest_summary = self._repo.get_latest_summary(session_id)
        summary_upto = latest_summary["upto_message_id"] if latest_summary else 0
        existing_summary = latest_summary["summary"] if latest_summary else ""

        pending = latest_id - summary_upto
        if pending < window_size + trigger:
            return

        to_id = latest_id - window_size
        from_id = summary_upto + 1

        messages = self._repo.get_messages_range(session_id, from_id, to_id)
        if not messages:
            return

        lines = []
        for m in messages:
            role = m["role"]
            content = (m["content"] or "").strip()
            outs = m.get("outputs") or []
            if outs:
                out_desc = ", ".join(
                    f"{o.get('kind')}={o.get('filename', '')[:40]}"
                    for o in outs if isinstance(o, dict)
                )
                content = f"{content} [产物: {out_desc}]"
            lines.append(f"{role}: {content[:500]}")
        new_dialogue = "\n".join(lines)

        prompt = _SUMMARY_PROMPT_TMPL.format(
            existing_summary=existing_summary or "（无）",
            new_dialogue=new_dialogue,
            max_chars=max_chars,
        )

        try:
            # ⭐ 摘要是后台线程的长 LLM 调用，独立抢锁；超时 180s
            with gpu_lock.lease(GPUResource.OLLAMA_CHAT, task="summary", timeout=180):
                result = ollama_client.chat(
                    service=svc, model_key=mkey,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                )
            new_summary = (result or "").strip()
            if len(new_summary) < 10:
                return
            if len(new_summary) > max_chars * 2:
                new_summary = new_summary[:max_chars * 2]

            self._repo.save_summary(
                session_id=session_id, upto_message_id=to_id,
                summary=new_summary, msg_count=len(messages),
            )
            logger.info(
                f"[Context] 摘要完成 session={session_id[:8]} "
                f"upto={to_id} msgs={len(messages)} chars={len(new_summary)}"
            )
        except Exception as e:
            logger.warning(f"[Context] 摘要生成失败: {e}")


conversation_service = ConversationService()