# app/skills/code/workspace/config.py
from __future__ import annotations

from app.config.loader import config


def _get(key: str, default):
    try:
        return config.get(key, default)
    except Exception:
        return default


class CodeWorkspaceConfig:
    @property
    def ttl_hours(self) -> int:
        return int(_get("code_workspace.ttl_hours", 24))

    @property
    def ttl_seconds(self) -> int:
        return self.ttl_hours * 3600

    @property
    def max_total_mb(self) -> int:
        return int(_get("code_workspace.max_total_mb", 200))

    @property
    def max_file_mb(self) -> int:
        return int(_get("code_workspace.max_file_mb", 2))

    @property
    def max_files(self) -> int:
        return int(_get("code_workspace.max_files", 3000))

    @property
    def upload_batch_files(self) -> int:
        return int(_get("code_workspace.upload_batch_files", 30))

    @property
    def map_model(self) -> str:
        return str(_get("code_scan.map_model", "code_fast"))

    @property
    def reduce_model(self) -> str:
        return str(_get("code_scan.reduce_model", "code_think"))

    @property
    def chunk_tokens(self) -> int:
        return int(_get("code_scan.chunk_tokens", 1200))

    @property
    def chunk_overlap_tokens(self) -> int:
        return int(_get("code_scan.chunk_overlap_tokens", 120))

    @property
    def map_max_tokens(self) -> int:
        return int(_get("code_scan.map_max_tokens", 800))

    @property
    def reduce_max_tokens(self) -> int:
        return int(_get("code_scan.reduce_max_tokens", 2000))

    @property
    def retry(self) -> int:
        return int(_get("code_scan.retry", 2))

    @property
    def token_est_ratio(self) -> int:
        return int(_get("code_scan.token_est_ratio", 3))

    @property
    def yield_sleep_ms(self) -> int:
        return int(_get("code_scan.yield_sleep_ms", 150))

    @property
    def context_recent_turns(self) -> int:
        return int(_get("code_context.recent_turns", 12))

    @property
    def context_max_tokens(self) -> int:
        return int(_get("code_context.max_context_tokens", 6000))

    @property
    def context_summary_trigger_tokens(self) -> int:
        return int(_get("code_context.summary_trigger_tokens", 4000))


code_cfg = CodeWorkspaceConfig()