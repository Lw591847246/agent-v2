"""任务模型（原 VideoTaskInfo 改名，加 outputs_json）。"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class TaskInfo(BaseModel):
    task_id: str
    session_id: str
    user_id: int = 0
    intent: str = ""
    skill_name: Optional[str] = None
    template_id: Optional[str] = None
    slots: dict[str, Any] = Field(default_factory=dict)

    provider: str = "local"
    provider_job_id: Optional[str] = None

    status: str = "pending"
    progress: int = 0
    duration_ms: int = 0
    msg: Optional[str] = None
    steps_json: Optional[str] = None
    parts_json: Optional[str] = None
    outputs_json: Optional[str] = None
    output_path: Optional[str] = None
    result_url: Optional[str] = None
    msg_id: Optional[int] = None
    error: Optional[str] = None

    created_at: Optional[str] = None
    finished_at: Optional[str] = None


# 兼容旧名
VideoTaskInfo = TaskInfo