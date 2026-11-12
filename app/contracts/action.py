# app/contracts/action.py
"""Action 契约：卡片点击请求。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ActionRequest:
    session_id: str
    card_id: str
    card_type: str = ""
    option_key: str = ""
    text: str = ""
    file_id: str = ""
    client_id: str = ""
    user_id: int = 0