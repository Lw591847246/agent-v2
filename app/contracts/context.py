"""请求上下文：跨层传递的标准化请求对象。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.models.attachment import Attachment


@dataclass
class RequestContext:
    text: str = ""
    user_id: int = 0
    session_id: str = ""
    attachments: List[Attachment] = field(default_factory=list)
    history: List[Dict] = field(default_factory=list)
    full_history: List[Dict] = field(default_factory=list)
    summary: str = ""
    recent_intents: List[str] = field(default_factory=list)
    params: Dict[str, Any] = field(default_factory=dict)
    flow_context: Optional[Dict[str, Any]] = None
    want_stream: bool = False

    def first_attachment(self, type_: str) -> Optional[Attachment]:
        for a in self.attachments:
            if a.type == type_:
                return a
        return None

    def images(self) -> List[Attachment]:
        return [a for a in self.attachments if a.type == "image"]

    def videos(self) -> List[Attachment]:
        return [a for a in self.attachments if a.type == "video"]

    def docs(self) -> List[Attachment]:
        return [a for a in self.attachments if a.type == "doc"]

    def audios(self) -> List[Attachment]:
        return [a for a in self.attachments if a.type == "audio"]

    def has_attachments(self) -> bool:
        return bool(self.attachments)