"""异步任务统一入口：create / progress / finish / fail / outputs 落库。"""
from __future__ import annotations

import json
import threading
import uuid
from typing import Callable, Optional

from app.core.logger import logger
from app.infrastructure.repositories.conversation_repo import conversation_repo
from app.infrastructure.repositories.task_repo import task_repo


class TaskRunner:
    def submit(
        self, skill_name: str, session_id: str, user_id: int,
        slots: dict, fn: Callable[[str], None],
        provider: str = "local",
    ) -> str:
        task_id = f"{skill_name}_{uuid.uuid4().hex[:10]}"
        task_repo.create_task(
            task_id=task_id, session_id=session_id, user_id=user_id,
            skill_name=skill_name, slots=slots or {}, provider=provider,
        )
        threading.Thread(target=self._run, args=(task_id, fn), daemon=True).start()
        logger.info(f"[TaskRunner] submitted task_id={task_id} skill={skill_name}")
        return task_id

    def _run(self, task_id: str, fn: Callable[[str], None]) -> None:
        try:
            fn(task_id)
        except Exception as e:
            logger.error(f"[TaskRunner] task_id={task_id} 执行失败: {e}", exc_info=True)
            self.fail(task_id, str(e))

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
        # ⭐ 1. 先更新会话消息（把图片/视频路径、输出等写入 agent_message）
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

        # ⭐ 2. 最后更新任务表状态（前端轮询拿到 done 时，消息表已确保更新完毕）
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
            return

        try:
            msg_id = conversation_repo.get_message_id_by_task_id(session_id, task_id)
            if msg_id:
                parts = [{"type": "text", "content": f"❌ 任务失败：{error}"}]
                conversation_repo.update_message_content(
                    message_id=msg_id,
                    content=json.dumps(parts, ensure_ascii=False),
                    outputs=[],
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
        if "audio" in types:      # ⭐ 新增：音频类型
            return "audio"
        if "card" in types:
            return "card"
        if len(parts) > 1:
            return "multi"
        return "text"


task_runner = TaskRunner()