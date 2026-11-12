from __future__ import annotations

from typing import Any, Optional

from app.infrastructure.clients.liveportrait_client import client, LivePortraitError


class LivePortraitService:
    """Thin orchestration layer over LivePortraitClient.

    Responsibilities:
      - list templates / voices / driving presets
      - proxy asset upload
      - submit render job
      - query job status
      - (download handled by controller returning a streaming response)
    """

    def __init__(self) -> None:
        self.client = client

    # ---------- catalogs ----------
    def list_templates(self, tag: Optional[str] = None, keyword: Optional[str] = None) -> list[dict]:
        return self.client.list_templates(tag=tag, keyword=keyword)

    def get_template(self, template_id: str) -> dict:
        return self.client.get_template(template_id)

    def list_voices(self) -> list[dict]:
        return self.client.list_voices()

    def list_driving(self) -> list[dict]:
        return self.client.list_driving()

    # ---------- assets ----------
    def upload_asset(self, file_bytes: bytes, filename: str, mime: Optional[str] = None) -> dict:
        return self.client.upload_asset(file_bytes, filename, mime)

    # ---------- render ----------
    def render(self, template_id: str, overrides: dict, preview: bool = False) -> dict:
        return self.client.render_by_template(
            template_id=template_id,
            overrides=overrides,
            preview=preview,
        )

    # ---------- jobs ----------
    def get_job(self, job_id: str) -> dict:
        return self.client.get_job(job_id)

    def cancel_job(self, job_id: str) -> dict:
        return self.client.cancel_job(job_id)


service = LivePortraitService()
