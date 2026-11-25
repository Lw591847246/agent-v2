# app/skills/code/workspace/mapreduce/schemas.py
from __future__ import annotations

import json
import re
from typing import Type, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def extract_json(text: str) -> dict:
    """从模型输出中提取 JSON；兼容 ```json fenced。"""
    s = (text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, re.S)
        if not m:
            raise ValueError("模型输出不是 JSON")
        return json.loads(m.group(0))


def parse_model(model: Type[T], text: str) -> T:
    return model(**extract_json(text))