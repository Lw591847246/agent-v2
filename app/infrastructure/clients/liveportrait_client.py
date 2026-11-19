from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

import httpx

from app.config.liveportrait_config import config


class LivePortraitError(RuntimeError):
    pass


class LivePortraitClient:
    """Thin HTTP wrapper around the LivePortrait service.

    Stateless. Safe to construct per-request.
    """

    def __init__(self, base_url: Optional[str] = None, timeout: Optional[int] = None) -> None:
        self.base_url = (base_url or config.base_url).rstrip("/")
        self.timeout = timeout or config.timeout_seconds
        # ⭐ 分层超时：httpx 用 httpx.Timeout 把 connect 单独收紧到秒级，read 维持原值；
        #    值优先从 liveportrait 配置读，缺省兜底，向后兼容不依赖 config 必须新增字段。
        self.connect_timeout = float(getattr(config, "connect_timeout", 5))
        self.upload_connect_timeout = float(getattr(config, "upload_connect_timeout", 5))
        self.download_connect_timeout = float(getattr(config, "download_connect_timeout", 10))

    # ---------- low level ----------
    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _timeout(self, read: float, connect: Optional[float] = None) -> httpx.Timeout:
        """read 维持原值，connect 单独收紧；缺省用 self.connect_timeout。"""
        c = self.connect_timeout if connect is None else connect
        return httpx.Timeout(timeout=float(read), connect=float(c))

    def _get_json(self, path: str) -> Any:
        with httpx.Client(timeout=self._timeout(self.timeout)) as c:
            r = c.get(self._url(path))
        if r.status_code >= 400:
            raise LivePortraitError(f"GET {path} -> {r.status_code}: {r.text[:300]}")
        return r.json()

    def _post_json(self, path: str, body: dict) -> Any:
        with httpx.Client(timeout=self._timeout(self.timeout)) as c:
            r = c.post(self._url(path), json=body)
        if r.status_code >= 400:
            raise LivePortraitError(f"POST {path} -> {r.status_code}: {r.text[:300]}")
        return r.json()

    # ---------- health ----------
    def health(self) -> dict:
        return self._get_json("/health")

    # ---------- templates ----------
    def list_templates(self, tag: Optional[str] = None, keyword: Optional[str] = None) -> list[dict]:
        params = []
        if tag:
            params.append(f"tag={tag}")
        if keyword:
            params.append(f"keyword={keyword}")
        path = "/v1/templates"
        if params:
            path += "?" + "&".join(params)
        return self._get_json(path)

    def get_template(self, template_id: str) -> dict:
        return self._get_json(f"/v1/templates/{template_id}")

    # ---------- voices ----------
    def list_voices(self) -> list[dict]:
        return self._get_json("/v1/voices")

    # ---------- driving ----------
    def list_driving(self) -> list[dict]:
        return self._get_json("/v1/driving")

    # ---------- assets ----------
    def upload_asset(self, file_bytes: bytes, filename: str, mime: Optional[str] = None) -> dict:
        files = {"file": (filename, file_bytes, mime or "application/octet-stream")}
        with httpx.Client(timeout=self._timeout(max(self.timeout, 120), self.upload_connect_timeout)) as c:
            r = c.post(self._url("/v1/assets/upload"), files=files)
        if r.status_code >= 400:
            raise LivePortraitError(f"upload -> {r.status_code}: {r.text[:300]}")
        return r.json()

    # ---------- render ----------
    def render_by_template(
        self,
        template_id: str,
        overrides: Optional[dict] = None,
        preview: bool = False,
    ) -> dict:
        body = {
            "template_id": template_id,
            "overrides": overrides or {},
            "preview": preview,
        }
        return self._post_json("/v1/render/by-template", body)

    # ---------- jobs ----------
    def get_job(self, job_id: str) -> dict:
        return self._get_json(f"/v1/jobs/{job_id}")

    def cancel_job(self, job_id: str) -> dict:
        return self._post_json(f"/v1/jobs/{job_id}/cancel", {})

    def wait_for(
        self,
        job_id: str,
        poll_interval: Optional[int] = None,
        max_wait: Optional[int] = None,
        on_progress=None,
    ) -> dict:
        """Blocking poll until job is done/failed/canceled."""
        interval = poll_interval or config.poll_interval_seconds
        deadline = time.time() + (max_wait or config.max_poll_seconds)

        while True:
            job = self.get_job(job_id)
            if on_progress:
                try:
                    on_progress(job)
                except Exception:
                    pass

            status = job.get("status")
            if status in ("done", "failed", "canceled"):
                return job

            if time.time() > deadline:
                raise LivePortraitError(f"wait_for timeout after {max_wait}s, job_id={job_id}")

            time.sleep(interval)

    def download_result(self, job_id: str, save_to: Path) -> Path:
        save_to.parent.mkdir(parents=True, exist_ok=True)
        with httpx.Client(timeout=self._timeout(max(self.timeout, 300), self.download_connect_timeout)) as c:
            with c.stream("GET", self._url(f"/v1/jobs/{job_id}/result")) as r:
                if r.status_code >= 400:
                    raise LivePortraitError(f"download -> {r.status_code}: {r.text[:300]}")
                with open(save_to, "wb") as f:
                    for chunk in r.iter_bytes():
                        f.write(chunk)
        return save_to


# singleton for reuse
client = LivePortraitClient()
