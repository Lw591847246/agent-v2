# app/infrastructure/repositories/token_repo.py
from __future__ import annotations

from typing import List, Dict

from app.core.logger import logger
from app.infrastructure.repositories.base_repo import BaseRepo


_INSERT_SQL = """
INSERT INTO agent_llm_call_log
(trace_id, session_id, user_id, service, model_key,
 input_tokens, output_tokens, total_tokens, cost,
 duration_ms, status, error_msg)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
"""


class TokenRepo(BaseRepo):

    def batch_insert(self, rows: List[Dict]) -> None:
        if not rows:
            return
        params = [(
            r.get("trace_id", "")[:32],
            r.get("session_id", "")[:64],
            int(r.get("user_id") or 0),
            r.get("service", "")[:32],
            r.get("model_key", "")[:64],
            int(r.get("input_tokens") or 0),
            int(r.get("output_tokens") or 0),
            int(r.get("input_tokens") or 0) + int(r.get("output_tokens") or 0),
            float(r.get("cost") or 0),
            int(r.get("duration_ms") or 0),
            (r.get("status") or "ok")[:16],
            (r.get("error_msg") or "")[:255] or None,
        ) for r in rows]

        try:
            with self.cursor(commit=True) as cur:
                cur.executemany(_INSERT_SQL, params)
        except Exception as e:
            logger.warning(f"[TokenRepo] batch_insert 失败 rows={len(params)} err={e}")


token_repo = TokenRepo()