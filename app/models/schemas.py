from pydantic import BaseModel
from typing import List, Optional, Dict, Any


class ChatRequest(BaseModel):
    text: str
    session_id: Optional[str] = None
    client_id: Optional[str] = None
    attachments: List["AttachmentRef"] = []
    # ⭐ 改为 Dict 接收前端传来的 { lat, lng } 对象
    flow_context: Optional[Dict[str, Any]] = None


class AttachmentRef(BaseModel):
    file_id: str
    type: str = "image"


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    realName: str
    phone: str
    password: str


class FileBase64Item(BaseModel):
    name: str
    base64: str


class ProcessFileRequest(BaseModel):
    session_id: str
    text: str = ""
    files: List[FileBase64Item] = []


class ChatActionRequest(BaseModel):
    session_id: str
    card_id: str
    card_type: str = ""
    option_key: str = ""
    text: str = ""
    client_id: str = ""
    file_id: Optional[str] = None


ChatRequest.model_rebuild()