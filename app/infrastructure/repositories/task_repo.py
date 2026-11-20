"""app/infrastructure/repositories/task_repo.py
统一任务仓储（V2）：finish 支持 outputs；新增启动期僵尸任务回收。

本批改动（定点）：fail() 写端收口节点状态。
- 旧行为：fail() 只写 status/error/finished_at，不碰 steps_json，任务进终态后
  最后一个 update_progress 写入的 running 节点被冻结在"转圈"态，前端出现
  「红色失败横幅 + 死掉的那个节点还在转圈」的反直觉画面。
- 新行为：fail() 在写终态的同一次更新里，读出现有 steps_json，把其中
  status=="running" 的节点改写为 "failed"（done 保持、未跑到的 pending 保持），
  一并写回。所有走 fail() 的业务（文生图/图生视频/…）自动复用同一套收口逻辑。
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Optional

from app.core.logger import logger
from app.domain.models.task import TaskInfo
from app.infrastructure.repositories.mysql_pool import mysql_pool
from app.application.failure_message_service import friendly_error


@contextmanager
def _conn():
    conn = mysql_pool.get_connection()
    try:
        yield conn
    finally:
        conn.close()


def _reconcile_steps_for_fail(steps: list) -> tuple[list, bool]:
    """任务进终态时对 steps 的收口（纯函数，便于离线单测）。

    规则：
    - running -> failed  （真正死在执行中的那个节点）
    - done    -> 保持不变（确实跑完的前置节点）
    - pending -> 保持不变（压根没轮到的节点，前端画灰）
    返回 (新 steps, 是否发生改写)。非 dict 元素原样保留、不改写。
    """
    changed = False
    new_steps = []
    for s in steps:
        if isinstance(s, dict) and s.get("status") == "running":
            s = {**s, "status": "failed"}
            changed = True
        new_steps.append(s)
    return new_steps, changed


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
            "msg_id",
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

    # ---------- msg_id 显式关联（根治补丁新增）----------
    def set_msg_id(self, task_id: str, msg_id: int) -> None:
        """把回填目标 assistant 消息 id 显式写回任务行。
        由 chat_service.finalize 在占位消息落库成功后调用，建立 task -> msg 的显式外键。
        """
        try:
            self.update(task_id, msg_id=int(msg_id))
            logger.info(f"[TaskRepo] set_msg_id task_id={task_id} msg_id={msg_id}")
        except Exception as e:
            logger.warning(f"[TaskRepo] set_msg_id failed task_id={task_id}: {e}")

    def get_msg_id(self, task_id: str, timeout: float = 2.0, interval: float = 0.2) -> int:
        """读取任务的显式 msg_id。

        根治点：后台任务（异步 skill 的 _run_async / task_runner.finish）不再用
        'content LIKE %task_id%' 反查占位消息，而是直接读 agent_video_task.msg_id。
        占位消息由 finalize 在请求返回时落库并回写，通常远早于后台任务完成；
        但秒级任务可能后台先于 finalize 完成，故这里带"等待重试"封死时序竞态：
        msg_id 尚未写入时轮询等待，超过 timeout 仍为空则返回 0（由调用方走兜底）。
        """
        import time
        deadline = time.time() + timeout
        while True:
            info = self.get(task_id)
            mid = getattr(info, "msg_id", None) if info else None
            if mid:
                return int(mid)
            if time.time() >= deadline:
                return 0
            time.sleep(interval)

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

        # ---------- fail（核心修复：节点收口 + 友好文案） ----------

    def fail(self, task_id: str, error: str) -> None:
        """
        统一失败写入口。所有业务调 task_repo.fail() 自动获得：
          1. 节点态收口：steps 中 running -> failed，done/pending 不动；
          2. error 字段存友好短句（原始异常仅进日志，不存 DB）。

        注意：调用方传入的 error 参数应为原始异常字符串（方便日志排查），
              本方法内部会调用 friendly_error() 转为友好文案后写入 DB。
              原始异常全文由调用方自行 logger.exception() 记录。
        """
        # 1. 友好文案（写 DB）
        display_error = friendly_error(error)

        # 2. 读取当前 steps 并收口
        fields: dict[str, Any] = {
            "status": "failed",
            "error": display_error,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            info = self.get(task_id)
            if info and info.steps_json:
                steps = json.loads(info.steps_json)
                if isinstance(steps, list):
                    has_change = False
                    for s in steps:
                        if isinstance(s, dict) and s.get("status") == "running":
                            s["status"] = "failed"
                            has_change = True
                    if has_change:
                        fields["steps_json"] = json.dumps(steps, ensure_ascii=False)
        except Exception:
            pass  # 收口失败不影响主流程

        self.update(task_id, **fields)

    # def fail(self, task_id: str, error: str) -> None:
    #     """失败终态写入口：同步收口 steps 节点状态。
    #
    #     写 status=failed / error / finished_at 的同时，读出现有 steps_json，
    #     把其中正在 running 的节点改写为 failed（done 保持、pending 保持），
    #     在同一次更新内写回。这样任务进终态后，前端不会再看到「失败横幅 +
    #     死掉的那个节点还在转圈」的错位画面。
    #     解析 steps_json 失败 / 无 steps / 没有 running 节点时，只落终态、
    #     不写 steps_json，不影响 fail 主流程。
    #     """
    #     fields = {
    #         "status": "failed",
    #         "error": error,
    #         "finished_at": datetime.now().isoformat(timespec="seconds"),
    #     }
    #     try:
    #         info = self.get(task_id)
    #         if info is not None and getattr(info, "steps_json", None):
    #             steps = json.loads(info.steps_json)
    #             if isinstance(steps, list):
    #                 new_steps, changed = _reconcile_steps_for_fail(steps)
    #                 if changed:
    #                     fields["steps_json"] = new_steps
    #                     logger.info(
    #                         "[TaskRepo] fail 收口节点态 task_id=%s steps=%s"
    #                         % (task_id, [s.get("status") for s in new_steps if isinstance(s, dict)])
    #                     )
    #     except Exception as e:
    #         logger.warning(
    #             "[TaskRepo] fail 收口 steps 跳过（不影响失败落库）task_id=%s err=%s" % (task_id, e)
    #         )
    #     self.update(task_id, **fields)

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
        msg_id=(int(row["msg_id"]) if row.get("msg_id") not in (None, "") else None),
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

