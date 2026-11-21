# app/infrastructure/repositories/conversation_repo.py
"""会话仓储（V2）：支持 outputs 落库。"""
import json
from typing import Any, Dict, List, Optional

from app.core.logger import logger
from app.domain.repositories.conversation_repository import ConversationRepository
from app.infrastructure.repositories.base_repo import BaseRepo
from app.utils.signed_url import sign


class ConversationRepoImpl(BaseRepo, ConversationRepository):

    # ============ 会话 ============

    def get_all_messages(self, session_id, user_id=None):
        if not user_id:
            return []
        rows = self.query_list(
            "SELECT m.id, m.role, m.content, m.message_type, m.outputs, "
            "m.image_filename, m.thumb_filename, m.client_id, m.duration_ms, m.created_at "
            "FROM agent_message m "
            "JOIN agent_conversation c ON m.session_id = c.session_id "
            "WHERE m.session_id=%s AND c.user_id=%s "
            "ORDER BY m.id ASC",
            (session_id, user_id),
        )
        result = []
        for r in rows:
            item = {
                "id": r["id"],
                "role": r["role"],
                "content": r["content"],
                "message_type": r.get("message_type") or "text",
                "client_id": r.get("client_id"),
                "duration_ms": r.get("duration_ms"),
                "created_at": r["created_at"].strftime("%Y-%m-%d %H:%M:%S") if r["created_at"] else None,
            }
            if item["message_type"] == "image":
                # ⭐ 带 HMAC 签名，防枚举
                sig = sign(r["id"])
                item["image_url"] = f"/agent/v1/image/{r['id']}?sig={sig}"
                if r.get("thumb_filename"):
                    item["thumb_url"] = f"/agent/v1/thumb/{r['id']}?sig={sig}"
            outputs = r.get("outputs")
            if isinstance(outputs, str):
                try:
                    outputs = json.loads(outputs)
                except Exception:
                    outputs = None
            if outputs:
                item["outputs"] = outputs
            result.append(item)

        logger.info(f"[Repo] get_all_messages session={session_id}, {len(result)} 条")
        return result

    def exists(self, session_id: str) -> bool:
        row = self.query_one(
            "SELECT 1 FROM agent_conversation WHERE session_id=%s LIMIT 1",
            (session_id,),
        )
        return row is not None

    def create(self, session_id: str, user_id: str, title: str = None):
        self.execute(
            "INSERT INTO agent_conversation (session_id, user_id, title) VALUES (%s,%s,%s)",
            (session_id, user_id, title),
        )

    def update_title(self, session_id: str, title: str):
        self.execute(
            "UPDATE agent_conversation SET title=%s WHERE session_id=%s",
            (title, session_id),
        )

    def list_by_user(self, user_id: str) -> List[dict]:
        rows = self.query_list(
            "SELECT session_id, title, updated_at FROM agent_conversation "
            "WHERE user_id=%s ORDER BY updated_at DESC",
            (user_id,),
        )
        return [
            {
                "session_id": r["session_id"],
                "title": r["title"],
                "updated_at": r["updated_at"].strftime("%Y-%m-%d %H:%M:%S") if r["updated_at"] else None,
            }
            for r in rows
        ]

    # ============ 消息 ============

    def save_message(
            self, session_id: str, role: str, content: str,
            message_type: str = "text",
            image_filename: str = None, thumb_filename: str = None,
            client_id: str = None, duration_ms: int = None,
    ) -> int:
        sql = """
            INSERT INTO agent_message
                (session_id, role, content, message_type,
                 image_filename, thumb_filename, client_id, duration_ms)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        with self.cursor(commit=True) as cur:
            cur.execute(sql, (
                session_id, role, content, message_type,
                image_filename, thumb_filename, client_id, duration_ms,
            ))
            return cur.lastrowid

    def exists_user_message_by_client_id(self, session_id: str, client_id: str) -> bool:
        if not client_id:
            return False
        row = self.query_one(
            "SELECT id FROM agent_message "
            "WHERE session_id=%s AND client_id=%s AND role='user' LIMIT 1",
            (session_id, client_id),
        )
        return row is not None

    def get_messages(self, session_id: str, limit: int = 20) -> List[dict]:
        rows = self.query_list(
            "SELECT role, content FROM agent_message WHERE session_id=%s "
            "ORDER BY id DESC LIMIT %s",
            (session_id, limit),
        )
        rows = list(rows)
        rows.reverse()
        return [{"role": r["role"], "content": r["content"]} for r in rows]

    def exists_for_user(self, session_id: str, user_id) -> bool:
        if not user_id:
            return False
        row = self.query_one(
            "SELECT 1 FROM agent_conversation "
            "WHERE session_id=%s AND user_id=%s LIMIT 1",
            (session_id, user_id),
        )
        return row is not None

    # def get_all_messages(self, session_id, user_id=None):
    #     if not user_id:
    #         return []
    #     rows = self.query_list(
    #         "SELECT m.id, m.role, m.content, m.message_type, m.outputs, "
    #         "m.image_filename, m.thumb_filename, m.client_id, m.duration_ms, m.created_at "
    #         "FROM agent_message m "
    #         "JOIN agent_conversation c ON m.session_id = c.session_id "
    #         "WHERE m.session_id=%s AND c.user_id=%s "
    #         "ORDER BY m.id ASC",
    #         (session_id, user_id),
    #     )
    #     result = []
    #     for r in rows:
    #         item = {
    #             "id": r["id"],
    #             "role": r["role"],
    #             "content": r["content"],
    #             "message_type": r.get("message_type") or "text",
    #             "client_id": r.get("client_id"),
    #             "duration_ms": r.get("duration_ms"),
    #             "created_at": r["created_at"].strftime("%Y-%m-%d %H:%M:%S") if r["created_at"] else None,
    #         }
    #         if item["message_type"] == "image":
    #             item["image_url"] = f"/agent/v1/image/{r['id']}"
    #             if r.get("thumb_filename"):
    #                 item["thumb_url"] = f"/agent/v1/thumb/{r['id']}"
    #         outputs = r.get("outputs")
    #         if isinstance(outputs, str):
    #             try:
    #                 outputs = json.loads(outputs)
    #             except Exception:
    #                 outputs = None
    #         if outputs:
    #             item["outputs"] = outputs
    #         result.append(item)
    #
    #     logger.info(f"[Repo] get_all_messages session={session_id}, {len(result)} 条")
    #     return result

    def message_count(self, session_id: str) -> int:
        row = self.query_one(
            "SELECT COUNT(*) AS cnt FROM agent_message WHERE session_id=%s",
            (session_id,),
        )
        return row["cnt"] if row else 0

    def get_message_by_id(self, message_id: int) -> dict:
        return self.query_one(
            "SELECT id, session_id, role, content, message_type, image_filename "
            "FROM agent_message WHERE id=%s",
            (message_id,),
        )

    def get_message_id_by_task_id(self, session_id: str, task_id: str) -> int:
        row = self.query_one(
            "SELECT id FROM agent_message "
            "WHERE session_id=%s AND role='assistant' AND content LIKE %s "
            "ORDER BY id DESC LIMIT 1",
            (session_id, f"%{task_id}%"),
        )
        return row["id"] if row else 0

    def save_assistant_message(
        self, session_id: str, content: str,
        message_type: str = "text",
        outputs: Optional[List[Dict[str, Any]]] = None,
        image_filename: Optional[str] = None,
        thumb_filename: Optional[str] = None,
        duration_ms: Optional[int] = None,
        project_id: Optional[int] = None,
        input_tokens: Optional[int] = 0,
        output_tokens: Optional[int] = 0,
        total_cost: Optional[float] = 0.0
    ) -> int:
        outputs_json = json.dumps(outputs, ensure_ascii=False) if outputs else None
        sql = """
            INSERT INTO agent_message
                (session_id, role, content, message_type, outputs,
         image_filename, thumb_filename, duration_ms, project_id,
         input_tokens, output_tokens, total_cost)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """
        with self.cursor(commit=True) as cur:
            cur.execute(sql, (
                session_id, "assistant", content, message_type, outputs_json,
                image_filename, thumb_filename, duration_ms, project_id,
                input_tokens, output_tokens, total_cost,
            ))
            mid = cur.lastrowid
            logger.info(f"[Repo] save_assistant_message id={mid} type={message_type}")
            return mid

    def update_message_content(
        self, message_id, content, duration_ms=None,
        image_filename=None, thumb_filename=None, message_type=None,
        outputs=None,
    ):
        """
        V2 新增：支持 outputs 落库。
        - outputs 为 None：不动 outputs 字段
        - outputs 为 []：清空
        - outputs 为 list：写入
        """
        try:
            fields = ["content=%s"]
            params = [content]
            if duration_ms is not None:
                fields.append("duration_ms=%s")
                params.append(duration_ms)
            if image_filename is not None:
                fields.append("image_filename=%s")
                params.append(image_filename)
            if thumb_filename is not None:
                fields.append("thumb_filename=%s")
                params.append(thumb_filename)
            if message_type is not None:
                fields.append("message_type=%s")
                params.append(message_type)
            if outputs is not None:
                fields.append("outputs=%s")
                params.append(json.dumps(outputs, ensure_ascii=False) if outputs else None)
            params.append(message_id)
            sql = f"UPDATE agent_message SET {', '.join(fields)} WHERE id=%s"
            self.execute(sql, tuple(params))
            logger.info(f"[Repo] update_message_content id={message_id} 成功 outputs={'✓' if outputs else '✗'}")
            return True
        except Exception as e:
            logger.error(f"[Repo] update_message_content id={message_id} 失败: {e}")
            return False

    # ============ outputs 引用 ============

    def get_recent_outputs(
        self, session_id: str, limit: int = 20,
        before_message_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        if before_message_id is not None:
            sql = """
                SELECT id, outputs, image_filename, thumb_filename, message_type
                FROM agent_message
                WHERE session_id=%s AND role='assistant'
                  AND (outputs IS NOT NULL OR image_filename IS NOT NULL)
                  AND id < %s
                ORDER BY id DESC LIMIT %s
            """
            params = (session_id, before_message_id, limit)
        else:
            sql = """
                SELECT id, outputs, image_filename, thumb_filename, message_type
                FROM agent_message
                WHERE session_id=%s AND role='assistant'
                  AND (outputs IS NOT NULL OR image_filename IS NOT NULL)
                ORDER BY id DESC LIMIT %s
            """
            params = (session_id, limit)

        rows = list(self.query_list(sql, params))
        rows.reverse()

        result: List[Dict[str, Any]] = []
        for r in rows:
            outputs = r.get("outputs")
            if isinstance(outputs, str):
                try:
                    outputs = json.loads(outputs)
                except Exception:
                    outputs = None
            if not outputs and r.get("image_filename"):
                outputs = [{
                    "kind": "image",
                    "filename": r["image_filename"],
                    "thumb": r.get("thumb_filename"),
                }]
            if outputs:
                result.append({"message_id": r["id"], "outputs": outputs})

        logger.info(f"[Repo] get_recent_outputs session={session_id}, {len(result)} 条")
        return result

    # ============ 上下文 ============

    def get_recent_messages(
        self, session_id: str, limit: int = 20,
        before_message_id: Optional[int] = None,
        include_outputs: bool = False,
    ) -> List[Dict[str, Any]]:
        if include_outputs:
            sql = """
                SELECT id, role, content, message_type, outputs,
                       image_filename, thumb_filename
                FROM agent_message WHERE session_id=%s
                {before} ORDER BY id DESC LIMIT %s
            """.format(before="AND id < %s" if before_message_id is not None else "")
            params = (session_id, before_message_id, limit) if before_message_id is not None else (session_id, limit)
        else:
            sql = """
                SELECT id, role, content, message_type
                FROM agent_message WHERE session_id=%s
                {before} ORDER BY id DESC LIMIT %s
            """.format(before="AND id < %s" if before_message_id is not None else "")
            params = (session_id, before_message_id, limit) if before_message_id is not None else (session_id, limit)

        rows = list(self.query_list(sql, params))
        rows.reverse()

        result = []
        for r in rows:
            item = {
                "id": r["id"],
                "role": r["role"],
                "content": r["content"],
                "message_type": r.get("message_type") or "text",
            }
            if include_outputs:
                outputs = r.get("outputs")
                if isinstance(outputs, str):
                    try:
                        outputs = json.loads(outputs)
                    except Exception:
                        outputs = None
                if not outputs and r.get("image_filename"):
                    outputs = [{"kind": "image", "filename": r["image_filename"], "thumb": r.get("thumb_filename")}]
                item["outputs"] = outputs or []
            result.append(item)
        return result

    def get_max_message_id(self, session_id: str) -> int:
        row = self.query_one(
            "SELECT MAX(id) AS max_id FROM agent_message WHERE session_id=%s",
            (session_id,),
        )
        if not row or row.get("max_id") is None:
            return 0
        return int(row["max_id"])

    def get_messages_range(self, session_id: str, from_id: int, to_id: int) -> List[Dict[str, Any]]:
        sql = """
            SELECT id, role, content, message_type, outputs,
                   image_filename, thumb_filename
            FROM agent_message
            WHERE session_id=%s AND id >= %s AND id <= %s
            ORDER BY id ASC
        """
        rows = self.query_list(sql, (session_id, from_id, to_id))
        result = []
        for r in rows:
            outputs = r.get("outputs")
            if isinstance(outputs, str):
                try:
                    outputs = json.loads(outputs)
                except Exception:
                    outputs = None
            if not outputs and r.get("image_filename"):
                outputs = [{"kind": "image", "filename": r["image_filename"], "thumb": r.get("thumb_filename")}]
            result.append({
                "id": r["id"],
                "role": r["role"],
                "content": r["content"],
                "message_type": r.get("message_type") or "text",
                "outputs": outputs or [],
            })
        return result

    # ============ summary / intents ============

    def get_latest_summary(self, session_id: str) -> Optional[Dict[str, Any]]:
        row = self.query_one(
            "SELECT id, upto_message_id, summary, msg_count, created_at "
            "FROM agent_conversation_summary WHERE session_id=%s ORDER BY id DESC LIMIT 1",
            (session_id,),
        )
        if not row:
            return None
        return {
            "id": int(row["id"]),
            "upto_message_id": int(row["upto_message_id"]),
            "summary": row["summary"],
            "msg_count": int(row.get("msg_count") or 0),
        }

    def save_summary(self, session_id: str, upto_message_id: int, summary: str, msg_count: int = 0) -> int:
        sql = """
            INSERT INTO agent_conversation_summary
                (session_id, upto_message_id, summary, msg_count)
            VALUES (%s, %s, %s, %s)
        """
        with self.cursor(commit=True) as cur:
            cur.execute(sql, (session_id, upto_message_id, summary, msg_count))
            return int(cur.lastrowid)

    def get_recent_intents(self, session_id: str) -> List[str]:
        row = self.query_one(
            "SELECT recent_intents FROM agent_session_state WHERE session_id=%s",
            (session_id,),
        )
        if not row or not row.get("recent_intents"):
            return []
        raw = row["recent_intents"]
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                return []
        return [str(x) for x in (raw or []) if x]

    def save_recent_intents(self, session_id: str, intents: List[str]) -> None:
        sql = """
            INSERT INTO agent_session_state (session_id, recent_intents)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE recent_intents=VALUES(recent_intents)
        """
        self.execute(sql, (session_id, json.dumps(intents or [], ensure_ascii=False)))

    def get_summary(self, session_id: str) -> Optional[str]:
        latest = self.get_latest_summary(session_id)
        return latest["summary"] if latest else None


conversation_repo: ConversationRepository = ConversationRepoImpl()