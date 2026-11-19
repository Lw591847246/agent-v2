# app/infrastructure/repositories/task_repo.py
"""统一任务仓储（V2）：finish 支持 outputs；新增启动期僵尸任务回收。"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Optional

from app.core.logger import logger
from app.domain.models.task import TaskInfo
from app.infrastructure.repositories.mysql_pool import mysql_pool


@contextmanager
def _conn():
    conn = mysql_pool.get_connection()
    try:
        yield conn
    finally:
        conn.close()


class TaskRepo:

    # ---------- create ----------
    def create(self, info: TaskInfo) -> None:
        sql = """
            INSERT INTO agent_video_task
                (task_id, session_id, user_id, intent, skill_name, template_id,
                 slots_json, provider, provider_job_id, status, progress,
                 output_path, error)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """
        with _conn() as c:
            with c.cursor() as cur:
                cur.execute(sql, (
                    info.task_id, info.session_id, info.user_id,
                    info.intent, info.skill_name or info.intent, info.template_id,
                    json.dumps(info.slots, ensure_ascii=False),
                    info.provider, info.provider_job_id,
                    info.status, info.progress, info.output_path, info.error,
                ))
            c.commit()
        logger.info(f"[TaskRepo] created task_id={info.task_id} skill={info.skill_name}")

    def create_task(
        self, task_id: str, session_id: str, user_id: int,
        skill_name: str, slots: dict,
        provider: str = "local", template_id: Optional[str] = None,
        intent: Optional[str] = None,
    ) -> None:
        self.create(TaskInfo(
            task_id=task_id, session_id=session_id, user_id=user_id,
            intent=intent or skill_name, skill_name=skill_name,
            template_id=template_id, slots=slots or {},
            provider=provider, status="pending", progress=0,
        ))

    # ---------- update ----------
    def update(self, task_id: str, **fields: Any) -> None:
        if not fields:
            return
        allowed = {
            "provider_job_id", "status", "progress",
            "output_path", "error", "finished_at", "slots",
            "skill_name", "msg", "steps_json", "parts_json", "outputs_json", "duration_ms",
        }
        cols, vals = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k == "slots":
                cols.append("slots_json=%s")
                vals.append(json.dumps(v, ensure_ascii=False))
            elif k in ("steps_json", "parts_json", "outputs_json"):
                cols.append(f"{k}=%s")
                vals.append(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
            else:
                cols.append(f"{k}=%s")
                vals.append(v)
        if not cols:
            return
        sql = f"UPDATE agent_video_task SET {', '.join(cols)} WHERE task_id=%s"
        vals.append(task_id)
        with _conn() as c:
            with c.cursor() as cur:
                cur.execute(sql, tuple(vals))
            c.commit()

    def update_progress(self, task_id: str, progress: int, msg: Optional[str] = None, steps: Optional[list] = None) -> None:
        fields = {"progress": progress, "status": "running"}
        if msg is not None:
            fields["msg"] = msg
        if steps is not None:
            fields["steps_json"] = json.dumps(steps, ensure_ascii=False)
        self.update(task_id, **fields)

    # ---------- finish ----------
    def finish(
        self, task_id: str, parts: list, duration_ms: int = 0,
        output_path: Optional[str] = None,
        outputs: Optional[list] = None,
    ) -> None:
        """V2：支持 outputs 落库。"""
        fields = {
            "status": "done",
            "progress": 100,
            "parts_json": json.dumps(parts, ensure_ascii=False),
            "duration_ms": duration_ms,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        }
        if output_path:
            fields["output_path"] = output_path
        if outputs is not None:
            fields["outputs_json"] = json.dumps(outputs, ensure_ascii=False)
        self.update(task_id, **fields)

    def fail(self, task_id: str, error: str) -> None:
        self.update(
            task_id,
            status="failed", error=error,
            finished_at=datetime.now().isoformat(timespec="seconds"),
        )

    # ---------- read ----------
    def get_progress(self, task_id: str) -> Optional[dict]:
        info = self.get(task_id)
        if not info:
            return None
        steps, parts, outputs = [], [], []
        for field, target in (("steps_json", steps), ("parts_json", parts), ("outputs_json", outputs)):
            raw = getattr(info, field, None)
            if raw:
                try:
                    target.extend(json.loads(raw))
                except Exception:
                    pass
        return {
            "status": info.status,
            "progress": info.progress,
            "msg": info.msg or "",
            "duration_ms": info.duration_ms or 0,
            "steps": steps,
            "parts": parts,
            "outputs": outputs,
        }

    def get(self, task_id: str) -> Optional[TaskInfo]:
        sql = "SELECT * FROM agent_video_task WHERE task_id=%s LIMIT 1"
        with _conn() as c:
            with c.cursor() as cur:
                cur.execute(sql, (task_id,))
                row = cur.fetchone()
        if not row:
            return None
        return _row_to_info(row)

    def list_by_session(self, session_id: str, limit: int = 20) -> list[TaskInfo]:
        sql = (
            "SELECT * FROM agent_video_task WHERE session_id=%s "
            "ORDER BY created_at DESC LIMIT %s"
        )
        with _conn() as c:
            with c.cursor() as cur:
                cur.execute(sql, (session_id, limit))
                rows = cur.fetchall()
        return [_row_to_info(r) for r in rows]

    # ---------- 僵尸任务回收（启动钩子调用）----------
    def mark_timeout_running_as_failed(self, timeout_minutes: int = 120) -> int:
        """把创建时间超过 timeout_minutes 仍处于 pending/running 的任务标记为 failed。

        用途：进程被 OOM Killer 杀掉后重启，DB 里会残留永远卡在 running 的僵尸任务。
        在 main.py lifespan 启动时调用一次即可。

        安全点：
        - 只清"创建时间早于 NOW()-timeout_minutes"的记录，绝不误杀刚启动的正常任务；
        - timeout_minutes 要大于系统里最长的正常任务耗时（默认 120，你的 INDEXTTS2 约 100min）；
        - 多 worker 部署下不能用进程启动时间做基准，这里用 created_at 超时窗口相对安全，
          但仍建议配合 workers=1 使用。
        """
        sql = """
            UPDATE agent_video_task
            SET status='failed',
                error='进程重启导致任务中断（超时未完成，启动时自动回收）',
                finished_at=NOW()
            WHERE status IN ('pending', 'running')
              AND created_at < NOW() - INTERVAL %s MINUTE
        """
        with _conn() as c:
            with c.cursor() as cur:
                n = cur.execute(sql, (timeout_minutes,))
            c.commit()
        if n:
            logger.warning(f"[TaskRepo] 启动回收僵尸任务 {n} 条（timeout={timeout_minutes}min）")
        else:
            logger.info("[TaskRepo] 启动回收：无僵尸任务需要清理")
        return n


def _row_to_info(row: dict) -> TaskInfo:
    slots = {}
    if row.get("slots_json"):
        try:
            slots = json.loads(row["slots_json"])
        except Exception:
            pass

    def _iso(v):
        if isinstance(v, datetime):
            return v.isoformat(timespec="seconds")
        return v

    status = row.get("status") or "pending"

    return TaskInfo(
        task_id=row["task_id"],
        session_id=row["session_id"],
        user_id=int(row.get("user_id") or 0),
        intent=row.get("intent") or "",
        skill_name=row.get("skill_name"),
        template_id=row.get("template_id"),
        slots=slots,
        provider=row.get("provider") or "local",
        provider_job_id=row.get("provider_job_id"),
        status=status,
        progress=int(row.get("progress") or 0),
        duration_ms=int(row.get("duration_ms") or 0),
        msg=row.get("msg"),
        steps_json=row.get("steps_json"),
        parts_json=row.get("parts_json"),
        outputs_json=row.get("outputs_json"),
        output_path=row.get("output_path"),
        result_url=f"/agent/v1/job/{row['task_id']}/video" if status == "done" else None,
        error=row.get("error"),
        created_at=_iso(row.get("created_at")),
        finished_at=_iso(row.get("finished_at")),
    )


task_repo = TaskRepo()
