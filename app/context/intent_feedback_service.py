# app/context/intent_feedback_service.py
"""意图反馈埋点：记录路由结果，供后续微调/分析"""
from typing import Optional

from app.core.logger import logger
from app.infrastructure.repositories.mysql_pool import mysql_pool


class IntentFeedbackService:

    def record(
        self,
        session_id: str,
        user_id: int,
        text: str,
        attachment_types: list,
        routing,
        message_id: Optional[int] = None,
    ) -> Optional[int]:
        try:
            if message_id is None:
                message_id = self._latest_user_msg_id(session_id)

            from app.scheduler.cache import intent_cache
            prompt_ver = getattr(intent_cache, "_version", "v0")

            sql = """
                INSERT INTO agent_intent_feedback
                    (session_id, user_id, message_id, user_input,
                     has_attachment, attachment_types,
                     intent_predicted, confidence, reasoning,
                     model_name, prompt_version,
                     feedback_type, user_action)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'none','ignore')
            """
            with mysql_pool.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (
                        session_id, user_id, message_id, text[:2000],
                        1 if attachment_types else 0,
                        ",".join(attachment_types) if attachment_types else None,
                        getattr(routing, "intent", None).value if hasattr(getattr(routing, "intent", None), "value") else str(getattr(routing, "intent", "")),
                        getattr(routing, "confidence", 0.0),
                        (getattr(routing, "reasoning", "") or "")[:500],
                        getattr(routing, "model_name", "") or "",
                        prompt_ver,
                    ))
                    fid = cur.lastrowid
                conn.commit()
            return fid
        except Exception as e:
            logger.warning(f"[IntentFeedback] record 失败（不阻塞）: {e}")
            return None

    @staticmethod
    def _latest_user_msg_id(session_id: str) -> Optional[int]:
        try:
            with mysql_pool.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id FROM agent_message "
                        "WHERE session_id=%s AND role='user' "
                        "ORDER BY id DESC LIMIT 1",
                        (session_id,),
                    )
                    row = cur.fetchone()
                    return row["id"] if row else None
        except Exception:
            return None


intent_feedback_service = IntentFeedbackService()