"""异步任务统一入口：create / progress / finish / fail / outputs 落库。

V2 加固：
- BoundedSemaphore 并发闸：物理掐断内存/DB 连接池/GPU 排队峰值
- submit 非阻塞：闸满直接拒绝，不拖住 web 线程
- trace_id / session_id / user_id 显式透传：threading.Thread 不继承 contextvar
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from typing import Callable, Optional

from app.core.context import RequestContext
from app.core.logger import logger
from app.infrastructure.repositories.conversation_repo import conversation_repo
from app.infrastructure.repositories.task_repo import task_repo


class TaskRunner:
    def __init__(self, max_concurrent: Optional[int] = None):
        # 默认 3，可用环境变量 TASK_MAX_CONCURRENT 调（按内存调整）
        n = max_concurrent or int(os.getenv("TASK_MAX_CONCURRENT", "3"))
        n = max(1, n)
        self._max_concurrent = n
        # BoundedSemaphore：误多 release 会抛 ValueError，防止闸被悄悄放大
        self._sem = threading.BoundedSemaphore(n)
        self._running = 0
        self._running_lock = threading.Lock()

    def submit(
        self, skill_name: str, session_id: str, user_id: int,
        slots: dict, fn: Callable[[str], None],
        provider: str = "local",
    ) -> str:
        # 非阻塞抢闸：满了直接拒绝，绝不在 web 线程里 acquire 阻塞
        if not self._sem.acquire(blocking=False):
            logger.warning(
                f"[TaskRunner] 并发已达上限({self._max_concurrent})，拒绝入队 "
                f"skill={skill_name} session={session_id}"
            )
            raise RuntimeError("系统繁忙，后台任务已达并发上限，请稍后重试")

        # 抢到闸后，下面任何一步失败都要把闸放回去，否则信号量漏放会越跑越小
        try:
            task_id = f"{skill_name}_{uuid.uuid4().hex[:10]}"
            task_repo.create_task(
                task_id=task_id, session_id=session_id, user_id=user_id,
                skill_name=skill_name, slots=slots or {}, provider=provider,
            )
        except Exception:
            self._sem.release()
            raise

        # threading.Thread 不继承 contextvar：主线程先取出来，传进子线程再 set
        trace_id = RequestContext.get_trace_id()
        threading.Thread(
            target=self._run,
            args=(task_id, fn, trace_id, session_id, str(user_id)),
            daemon=True,
        ).start()

        with self._running_lock:
            self._running += 1
            cur = self._running
        logger.info(
            f"[TaskRunner] submitted task_id={task_id} skill={skill_name} running={cur}"
        )
        return task_id

    def _run(
        self, task_id: str, fn: Callable[[str], None],
        trace_id: str, session_id: str, user_id: str,
    ) -> None:
        # 子线程入口重建请求上下文，使后台任务日志也能带上 trace_id
        RequestContext.set_trace_id(trace_id)
        RequestContext.set_session_id(session_id)
        RequestContext.set_user_id(user_id)
        RequestContext.set_extra_fields(task_type=task_id.split("_", 1)[0])
        try:
            fn(task_id)
        except Exception as e:
            logger.error(f"[TaskRunner] task_id={task_id} 执行失败: {e}", exc_info=True)
            self.fail(task_id, str(e))
        finally:
            with self._running_lock:
                self._running -= 1
            self._sem.release()
            RequestContext.clear_extra_fields()

    def progress(self, task_id: str, pct: int, msg: Optional[str] = None, steps: Optional[list] = None) -> None:
        try:
            task_repo.update_progress(task_id, pct, msg, steps=steps)
        except Exception as e:
            logger.warning(f"[TaskRunner] progress failed {task_id}: {e}")

    def finish(
        self,
        task_id: str,
        parts: list,
        outputs: Optional[list] = None,
        output_path: Optional[str] = None,
        duration_ms: int = 0,
        session_id: Optional[str] = None,
        message_extra: Optional[dict] = None,
    ) -> None:
        if not session_id:
            info = task_repo.get(task_id)
            session_id = info.session_id if info else None

        if session_id:
            try:
                msg_id = conversation_repo.get_message_id_by_task_id(session_id, task_id)
                if msg_id:
                    update_kwargs = {
                        "message_id": msg_id,
                        "content": json.dumps(parts, ensure_ascii=False),
                        "duration_ms": duration_ms,
                        "outputs": outputs or [],
                        "message_type": self._infer_type(parts),
                    }
                    if message_extra:
                        update_kwargs.update(message_extra)
                    conversation_repo.update_message_content(**update_kwargs)
                    logger.info(f"[TaskRunner] finish 已更新消息 msg_id={msg_id} task_id={task_id}")
                else:
                    logger.warning(f"[TaskRunner] finish 找不到 msg_id task_id={task_id}")
            except Exception as e:
                logger.warning(f"[TaskRunner] finish update_message failed: {e}")
        else:
            logger.warning(f"[TaskRunner] finish 找不到 session_id task_id={task_id}")

        try:
            task_repo.finish(
                task_id, parts=parts, duration_ms=duration_ms,
                output_path=output_path, outputs=outputs,
            )
            logger.info(f"[TaskRunner] finish 任务状态已更新为 done task_id={task_id}")
        except Exception as e:
            logger.error(f"[TaskRunner] finish task failed {task_id}: {e}")

    def fail(self, task_id: str, error: str, session_id: Optional[str] = None) -> None:
        try:
            task_repo.fail(task_id, error)
        except Exception as e:
            logger.warning(f"[TaskRunner] fail task failed {task_id}: {e}")

        if not session_id:
            info = task_repo.get(task_id)
            session_id = info.session_id if info else None
        if not session_id:
            logger.warning(f"[TaskRunner] fail 找不到 session_id task_id={task_id}")
            return

        try:
            msg_id = conversation_repo.get_message_id_by_task_id(session_id, task_id)
            if not msg_id:
                logger.warning(f"[TaskRunner] fail 找不到 msg_id task_id={task_id}")
                return

            # ⭐ 统一失败格式：error 卡片 + 兜底 text
            error_full = error or "未知错误"
            error_short = error_full[:500]

            parts = [
                {
                    "type": "card",
                    "card_type": "error",
                    "status": "failed",
                    "data": {
                        "title": "任务执行失败",
                        "description": error_short,
                        "error": error_short,
                    },
                },
                {
                    "type": "text",
                    "content": f"❌ 任务失败：{error_short}",
                },
            ]

            conversation_repo.update_message_content(
                message_id=msg_id,
                content=json.dumps(parts, ensure_ascii=False),
                outputs=[],
                message_type="multi",
            )
            logger.info(
                f"[TaskRunner] fail 已更新消息 msg_id={msg_id} "
                f"task_id={task_id} error_len={len(error_full)}"
            )
        except Exception as e:
            logger.warning(f"[TaskRunner] fail update_message failed: {e}")

    def cancel(self, task_id: str) -> None:
        try:
            task_repo.update(task_id, status="canceled", progress=0)
            logger.info(f"[TaskRunner] canceled task_id={task_id}")
        except Exception as e:
            logger.warning(f"[TaskRunner] cancel failed: {e}")

    @staticmethod
    def _infer_type(parts: list) -> str:
        types = {p.get("type") for p in parts or []}
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


task_runner = TaskRunner()
