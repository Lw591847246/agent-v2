"""会话仓储接口"""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class ConversationRepository(ABC):

    @abstractmethod
    def exists(self, session_id: str) -> bool: ...

    @abstractmethod
    def create(self, session_id: str, user_id: str, title: str = None): ...

    @abstractmethod
    def exists_user_message_by_client_id(self, session_id: str, client_id: str) -> bool:
        ...

    @abstractmethod
    def update_title(self, session_id: str, title: str): ...

    @abstractmethod
    def list_by_user(self, user_id: str) -> List[dict]: ...

    @abstractmethod
    def save_message(self, session_id: str, role: str, content: str): ...

    @abstractmethod
    def get_messages(self, session_id: str, limit: int = 20) -> List[dict]: ...

    @abstractmethod
    def get_all_messages(self, session_id: str, user_id: str = None) -> Optional[List[dict]]: ...

    @abstractmethod
    def message_count(self, session_id: str) -> int: ...

    @abstractmethod
    def save_assistant_message(
            self,
            session_id: str,
            content: str,
            message_type: str = "text",
            outputs: Optional[List[Dict[str, Any]]] = None,
            image_filename: Optional[str] = None,
            thumb_filename: Optional[str] = None,
            duration_ms: Optional[int] = None,
            project_id: Optional[int] = None,
    ) -> int:
        """保存 assistant 消息，返回 message_id"""
        ...

    @abstractmethod
    def get_recent_outputs(
            self,
            session_id: str,
            limit: int = 20,
            before_message_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        取最近 N 条 assistant 消息的产物列表（供引用解析用）。
        返回：[{"message_id": int, "outputs": [...]}, ...]
        注意：只返回有产物的消息；无 outputs 但有 image_filename 的旧数据也要兼容。
        """
        ...

    @abstractmethod
    def get_recent_messages(
            self,
            session_id: str,
            limit: int = 20,
            before_message_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """取最近 N 条消息（时间正序），支持 cursor 分页"""
        ...

    @abstractmethod
    def save_summary(
            self, session_id: str, upto_message_id: int, summary: str
    ) -> None:
        """stub：为长期记忆预留"""
        ...

    @abstractmethod
    def get_summary(self, session_id: str) -> Optional[str]:
        """stub：为长期记忆预留"""
        ...