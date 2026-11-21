"""异步任务统一入口：create / progress / finish / fail / outputs 落库。

V2 加固（保留）：
- BoundedSemaphore 并发闸：物理掐断内存/DB 连接池/GPU 排队峰值
- submit 非阻塞：闸满直接拒绝，不拖住 web 线程
- trace_id / session_id / user_id 显式透传：threading.Thread 不继承 contextvar

本轮新增（架构复核 P0-4）：
1. **每用户并发配额**：单用户同时运行 ≤ `task.max_per_user`（默认 2），
   防止一个人占满全局闸、把别人饿死；被拒时抛 RuntimeError（业务码统一处理）。
2. **指标埋点**：`agent_task_running`、`agent_task_rejected_total{reason}`、
   `agent_task_finished_total{status}`、`agent_task_queue_seconds{sum,count}`。
   其中 queue_seconds = submit → 第一次 progress 的间隔，近似"排队+预热"耗时。
3. 名额占用/释放收敛到 `_acquire_slot` / `_release_slot` 两个方法，
   避免"抢到闸但建任务行失败"时漏放（原来靠散落的 try/except）。
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from typing import Callable, Dict, Optional

from app.config.loader import config
from app.contracts.errors import SkillError
from app.core import metrics
from app.core.context import RequestContext
from app.core.logger import logger
from app.core.tracing import (
    capture_context, attach_context, detach_context, get_tracer, current_trace_ids,
)
from app.infrastructure.repositories.conversation_repo import conversation_repo
from app.infrastructure.repositories.task_repo import task_repo


class TaskRunner:
    def __init__(self, max_concurrent: Optional[int] = None,
                 max_per_user: Optional[int] = None):
        # 优先构造参数 > 环境变量 > yml(task.*) > 默认
        n = (max_concurrent
             or int(os.getenv("TASK_MAX_CONCURRENT", "0") or 0)
             or int(config.get("task.max_concurrent", 3) or 3))
        self._max_concurrent = max(1, n)

        m = (max_per_user
             or int(os.getenv("TASK_MAX_PER_USER", "0") or 0)
             or int(config.get("task.max_per_user", 2) or 2))
        self._max_per_user = max(1, m)

        # BoundedSemaphore：误多 release 会抛 ValueError，防止闸被悄悄放大
        self._sem = threading.BoundedSemaphore(self._max_concurrent)
        self._running = 0
        self._user_running: Dict[str, int] = {}
        self._lock = threading.Lock()
        self._submit_ts: Dict[str, float] = {}
        self._first_progress: Dict[str, bool] = {}

    # ============================================================
    # 名额（全局闸 + 每用户配额）统一管理
    # ============================================================
    def _acquire_slot(self, user_key: str, skill_name: str, session_id: str) -> int:
        """抢全局闸 + 用户配额。返回当前运行数；失败抛 RuntimeError。"""
        if not self._sem.acquire(blocking=False):
            metrics.inc("agent_task_rejected_total", {"reason": "global"})
            logger.warning(
                f"[TaskRunner] 全局并发已达上限({self._max_concurrent})，拒绝入队 "
                f"skill={skill_name} session={session_id}"
            )
            raise SkillError("系统繁忙，后台任务已达并发上限，请稍后重试", code=429)

        with self._lock:
            cur_user = self._user_running.get(user_key, 0)
            if cur_user >= self._max_per_user:
                self._sem.release()  # 立刻还回全局闸
                metrics.inc("agent_task_rejected_total", {"reason": "per_user"})
                logger.warning(
                    f"[TaskRunner] 用户并发已达上限({self._max_per_user})，拒绝入队 "
                    f"user={user_key} skill={skill_name}"
                )
                raise SkillError(
                    f"你还有 {cur_user} 个任务在运行，最多同时 {self._max_per_user} 个，请稍后重试",
                    code=429,
                )
            self._user_running[user_key] = cur_user + 1
            self._running += 1
            running = self._running

        metrics.set_gauge("agent_task_running", running)
        return running

    def _release_slot(self, user_key: str) -> None:
        with self._lock:
            cur = self._user_running.get(user_key, 0)
            if cur <= 1:
                self._user_running.pop(user_key, None)
            else:
                self._user_running[user_key] = cur - 1
            self._running = max(0, self._running - 1)
            running = self._running
        try:
            self._sem.release()
        except ValueError:
            logger.warning("[TaskRunner] 信号量重复释放（已忽略）")
        metrics.set_gauge("agent_task_running", running)

    # ============================================================
    # 提交
    # ============================================================
    def submit(
        self, skill_name: str, session_id: str, user_id: int,
        slots: dict, fn: Callable[[str], None],
        provider: str = "local",
    ) -> str:
        user_key = str(user_id)
        running = self._acquire_slot(user_key, skill_name, session_id)

        # 抢到名额后，下面任何一步失败都要还回去，否则名额会越跑越少
        try:
            task_id = f"{skill_name}_{uuid.uuid4().hex[:10]}"
            task_repo.create_task(
                task_id=task_id, session_id=session_id, user_id=user_id,
                skill_name=skill_name, slots=slots or {}, provider=provider,
            )
            self._submit_ts[task_id] = time.time()
            self._first_progress[task_id] = False
        except Exception:
            self._release_slot(user_key)
            raise

        # threading.Thread 不继承 contextvar：主线程先取出来，传进子线程再 set
        trace_id = RequestContext.get_trace_id()
        otel_ctx = capture_context()
        threading.Thread(
            target=self._run,
            args=(task_id, fn, trace_id, session_id, user_key, provider, otel_ctx),
            daemon=True,
        ).start()

        logger.info(
            f"[TaskRunner] submitted task_id={task_id} skill={skill_name} "
            f"user={user_key} running={running}"
        )
        return task_id

    def _run(
        self, task_id: str, fn: Callable[[str], None],
        trace_id: str, session_id: str, user_id: str,
        provider: str = "local", otel_ctx=None,
    ) -> None:
        # 子线程入口重建请求上下文，使后台任务日志也能带上 trace_id
        RequestContext.set_trace_id(trace_id)
        RequestContext.set_session_id(session_id)
        RequestContext.set_user_id(user_id)
        RequestContext.set_extra_fields(task_type=task_id.split("_", 1)[0])

        token = attach_context(otel_ctx)
        tracer = get_tracer("task_runner")
        sp = tracer.start_span(
            "task.run",
            attributes={
                "task.id": task_id,
                "task.skill": task_id.split("_", 1)[0],
                "task.provider": provider,
                "session.id": session_id,
                "app.trace_id": trace_id,
            },
        )
        try:
            ot_tid, _ = current_trace_ids()
            if ot_tid:
                sp.set_attribute("otel.trace_id", ot_tid)
            fn(task_id)
            sp.set_attribute("task.result", "done")
        except Exception as e:
            logger.error(f"[TaskRunner] task_id={task_id} 执行失败: {e}", exc_info=True)
            try:
                sp.set_attribute("task.result", "failed")
                sp.set_attribute("error", True)
                sp.set_attribute("error.message", str(e)[:500])
                sp.record_exception(e)
            except Exception:
                pass
            self.fail(task_id, str(e))
        finally:
            try:
                sp.end()
            except Exception:
                pass
            detach_context(token)
            self._submit_ts.pop(task_id, None)
            self._first_progress.pop(task_id, None)
            self._release_slot(user_id)
            RequestContext.clear_extra_fields()

    # ============================================================
    # 进度 / 完成 / 失败
    # ============================================================
    def progress(self, task_id: str, pct: int, msg: Optional[str] = None,
                 steps: Optional[list] = None) -> None:
        # 首次 progress ≈ 已排到队/抢到 GPU，用来度量排队耗时
        try:
            if self._first_progress.get(task_id) is False:
                self._first_progress[task_id] = True
                ts = self._submit_ts.get(task_id)
                if ts:
                    metrics.observe("agent_task_queue_seconds", time.time() - ts)
        except Exception:
            pass
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
                msg_id = task_repo.get_msg_id(task_id)
                if not msg_id:
                    # 兜底：仅当显式 msg_id 仍未就绪时退回旧的 LIKE 反查
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
            metrics.inc("agent_task_finished_total", {"status": "done"})
            logger.info(f"[TaskRunner] finish 任务状态已更新为 done task_id={task_id}")
        except Exception as e:
            logger.error(f"[TaskRunner] finish task failed {task_id}: {e}")

    def fail(self, task_id: str, error: str, session_id: Optional[str] = None) -> None:
        try:
            task_repo.fail(task_id, error)
            metrics.inc("agent_task_finished_total", {"status": "failed"})
        except Exception as e:
            logger.warning(f"[TaskRunner] fail task failed {task_id}: {e}")

        if not session_id:
            info = task_repo.get(task_id)
            session_id = info.session_id if info else None
        if not session_id:
            logger.warning(f"[TaskRunner] fail 找不到 session_id task_id={task_id}")
            return

        try:
            msg_id = task_repo.get_msg_id(task_id)
            if not msg_id:
                msg_id = conversation_repo.get_message_id_by_task_id(session_id, task_id)
            if not msg_id:
                logger.warning(f"[TaskRunner] fail 找不到 msg_id task_id={task_id}")
                return

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
            metrics.inc("agent_task_finished_total", {"status": "canceled"})
            logger.info(f"[TaskRunner] canceled task_id={task_id}")
        except Exception as e:
            logger.warning(f"[TaskRunner] cancel failed: {e}")

    # ============================================================
    # 运维视图
    # ============================================================
    def stats(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "max_concurrent": self._max_concurrent,
                "max_per_user": self._max_per_user,
                "users": dict(self._user_running),
            }

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