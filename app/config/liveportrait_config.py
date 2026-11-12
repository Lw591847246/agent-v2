from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class LivePortraitConfig:
    base_url: str
    timeout_seconds: int
    poll_interval_seconds: int
    max_poll_seconds: int

    @property
    def templates_url(self) -> str:
        return f"{self.base_url}/v1/templates"

    @property
    def voices_url(self) -> str:
        return f"{self.base_url}/v1/voices"

    @property
    def driving_url(self) -> str:
        return f"{self.base_url}/v1/driving"

    @property
    def upload_url(self) -> str:
        return f"{self.base_url}/v1/assets/upload"

    @property
    def render_url(self) -> str:
        return f"{self.base_url}/v1/render/by-template"

    def job_url(self, job_id: str) -> str:
        return f"{self.base_url}/v1/jobs/{job_id}"

    def job_result_url(self, job_id: str) -> str:
        return f"{self.base_url}/v1/jobs/{job_id}/result"

    def job_cancel_url(self, job_id: str) -> str:
        return f"{self.base_url}/v1/jobs/{job_id}/cancel"


config = LivePortraitConfig(
    base_url=os.environ.get("LIVEPORTRAIT_BASE_URL", "http://127.0.0.1:8001").rstrip("/"),
    timeout_seconds=int(os.environ.get("LIVEPORTRAIT_TIMEOUT", "60")),
    poll_interval_seconds=int(os.environ.get("LIVEPORTRAIT_POLL_INTERVAL", "3")),
    max_poll_seconds=int(os.environ.get("LIVEPORTRAIT_MAX_POLL", "1800")),
)
