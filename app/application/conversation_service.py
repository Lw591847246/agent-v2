"""会话服务。

本轮修复（越权写入 P0-1）新增会话准入三件套：
    is_owned / assert_owned / resolve_session
所有 "客户端传入 session_id" 的入口都必须先过 resolve_session：
    不存在 -> 幂等创建；存在但非本人 -> NotFoundException（不暴露存在性）。

注意：ensure_conversation 的旧语义是"不属于我就 create"，在无唯一约束的表上
会插入第二条归属记录、反而让攻击者能读到他人历史消息；现已收紧为
resolve_session 的别名。
"""
import os
import re
import threading
import uuid
from typing import Any, Dict, List, Optional
import contextvars
import concurrent.futures

from app.core.exceptions import NotFoundException
from app.core.gpu_lock import GPUResource, gpu_lock
from app.core.logger import logger
from app.domain.repositories.conversation_repository import ConversationRepository
from app.infrastructure.clients.ollama_client import ollama_client
from app.infrastructure.repositories.conversation_repo import conversation_repo
from app.utils.content_parser import parse_content_for_llm
from app.core.llm_tracker import track_llm

_summary_locks: Dict[str, threading.Lock] = {}

# session_id 会被下游 skill 直接当目录名用（data/videos/<uid>/<sid> 等），
# 因此只允许安全字符；uuid4().hex 与前端自生成的 id 都满足。
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# ============================================================
# 有界后台执行器
# ============================================================
_BG_MAX_WORKERS = int(os.getenv("CONV_BG_MAX_WORKERS", "2"))
_BG_QUEUE_CAP = int(os.getenv("CONV_BG_QUEUE_CAP", "16"))

_bg_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=_BG_MAX_WORKERS,
    thread_name_prefix="conv-bg",
)
_bg_sem = threading.BoundedSemaphore(_BG_MAX_WORKERS + _BG_QUEUE_CAP)


def submit_bg(task_label: str, fn, *args) -> bool:
    """把 best-effort 后台任务提交到有界执行器。

    返回是否成功提交；闸满立即返回 False，不阻塞调用方（Web 线程）。
    通过 contextvars.copy_context 把 trace_id 等上下文透传进 worker 线程，
    并显式恢复 OTel context（使后台 span 挂到发起请求的 trace 下）。
    """
    if not _bg_sem.acquire(blocking=False):
        logger.warning(
            f"[后台任务] 并发/队列已满，丢弃 best-effort 任务 label={task_label}"
        )
        return False

    ctx = contextvars.copy_context()
    try:
        from app.core.tracing import capture_context, run_in_context
        _otel_ctx = capture_context()
    except Exception:
        _otel_ctx = None
        run_in_context = None

    def _runner():
        try:
            if run_in_context is not None and _otel_ctx is not None:
                run_in_context(_otel_ctx, fn, *args)
            else:
                fn(*args)
        finally:
            _bg_sem.release()

    try:
        _bg_executor.submit(ctx.run, _runner)
    except Exception:
        _bg_sem.release()
        logger.warning(f"关闭期丢弃 label={task_label}")
        return False
    return True


def shutdown_conversation_background(wait: bool = False) -> None:
    """供应用 lifespan 关闭阶段调用，优雅收尾后台任务。"""
    try:
        _bg_executor.shutdown(wait=wait)
    except Exception as e:
        logger.warning(f"[后台任务] 关闭执行器异常: {e}")


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
    # 用户 ID 类型归一（不做 DDL 迁移）
    # ============================================================
    # 现状：Redis 里的 userId 是字符串，各 controller 又各自 int()，
    #       DB 列类型未知（INT 或 VARCHAR 都有可能）。
    # 结论：不做表结构迁移（收益小、涉及历史数据与前端），只在仓储边界统一成 str：
    #   - 列是 INT    -> WHERE user_id='123'，MySQL 把常量转成 int，索引照常命中；
    #   - 列是 VARCHAR -> 精确匹配，索引照常命中。
    # 反之传 int 时若列是 VARCHAR，列会被转成 double 比较，索引失效。
    @staticmethod
    def _db_uid(user_id) -> str:
        return str(int(user_id))

    # ============================================================
    # 会话归属（安全准入）
    # ============================================================

    def is_owned(self, session_id: str, user_id) -> bool:
        """会话是否属于该用户；空/非法一律 False。"""
        if not session_id or not user_id:
            return False
        try:
            return bool(self._repo.exists_for_user(session_id, self._db_uid(user_id)))
        except Exception as e:
            logger.warning(f"[会话] 归属查询失败 sid={session_id}: {e}")
            return False

    def assert_owned(self, session_id: str, user_id) -> str:
        """不属于该用户 -> NotFoundException（与 messages() 保持同一语义）。"""
        if not self.is_owned(session_id, user_id):
            logger.warning(
                f"[会话] 归属校验拒绝 sid={session_id} req_user={user_id}"
            )
            raise NotFoundException("会话不存在")
        return session_id

    def resolve_session(self, session_id: Optional[str], user_id) -> str:
        """HTTP 入口唯一准入。

        前提：会话只由后端创建（POST /agent/v1/conversation/new），前端只用返回值。
        因此**不再"不存在就创建"**：
        - 空 session_id            → 新建（兼容"首条消息不带 session"的老前端）
        - 格式非法(含 / \\ .. 空格) → 404 —— session_id 会被下游 skill 当目录名用
        - 不存在                   → 404（避免"认领"已删/他人话题的残留消息）
        - 存在但 owner != 当前用户 → 404
        - 存在且属于本人           → 复用（同一话题后续消息都走这条）

        若将来前端改成自造 session_id，把这里换成 "不存在则 create" 即可（见 git 历史）。
        """
        uid = int(user_id)
        sid = (session_id or "").strip()
        if not sid:
            return self.new_conversation(uid)

        if not _SESSION_ID_RE.match(sid):
            logger.warning(f"[会话] 非法 session_id 被拒绝: {sid!r} req_user={uid}")
            raise NotFoundException("会话不存在")

        if not self._repo.exists_for_user(sid, self._db_uid(uid)):
            logger.warning(f"[会话] 归属校验拒绝 sid={sid} req_user={uid}")
            raise NotFoundException("会话不存在")
        return sid

    def ensure_conversation(self, session_id: str, user_id) -> str:
        """兼容旧调用；语义已收紧为 resolve_session（不再"不属于我就 create"）。"""
        return self.resolve_session(session_id, user_id)

    # ============================================================
    # 会话
    # ============================================================

    def exists_user_message_by_client_id(self, session_id: str, client_id: str) -> bool:
        return self._repo.exists_user_message_by_client_id(session_id, client_id)

    def new_conversation(self, user_id) -> str:
        session_id = uuid.uuid4().hex
        self._repo.create(session_id, self._db_uid(user_id))
        return session_id

    def list(self, user_id) -> List[dict]:
        return self._repo.list_by_user(self._db_uid(user_id))

    def messages(self, session_id: str, user_id=None) -> dict:
        """读取会话消息：必须按 user_id 校验归属，拒绝越权访问。

        - 会话不存在 / 不属于该 user_id：统一抛 NotFoundException（避免暴露"存在但无权限"）
        - repo 层再做一次 user_id 过滤（SQL 双保险）
        """
        if not user_id or not self._repo.exists_for_user(session_id, self._db_uid(user_id)):
            raise NotFoundException("会话不存在")
        return {
            "session_id": session_id,
            "messages": self._repo.get_all_messages(session_id, self._db_uid(user_id)),
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

    def get_history(self, session_id: str, user_id=None) -> List[dict]:
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
        submit_bg("title-opt", self._async_optimize_title, session_id, text)

    def _async_optimize_title(self, session_id: str, text: str):
        try:
            prompt = (
                f"请为以下对话生成一个不超过 12 个字的简短标题，"
                f"只输出标题文字，不要标点、不要引号、不要解释：\n\n{text}"
            )
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

        try:
            submitted = submit_bg("summary", _run)
        except Exception:
            lock.release()
            raise
        if not submitted:
            lock.release()

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