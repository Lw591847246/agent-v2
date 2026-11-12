# app/skills/utility/service.py
"""Utility 业务：LLM 选工具 → 执行。"""
from __future__ import annotations

import json
from typing import Optional

from app.core.logger import logger
from app.infrastructure.clients.ollama_client import ollama_client
from app.skills.utility.prompts import UTILITY_SYSTEM_PROMPT
from app.skills.utility.tools import TOOLS_SCHEMA


class UtilityService:

    def handle(self, text: str) -> dict:
        """返回 {"type": "text", "content": "..."}"""
        if not text or not text.strip():
            return {"type": "text", "content": "请输入要执行的操作"}

        tool_name, args = self._select_tool(text)
        if not tool_name or tool_name == "none":
            return {"type": "text", "content": "这个请求不属于本地小工具范围，请换个问法"}

        logger.info(f"[UtilityService] 选择 tool={tool_name} args={args}")

        tool_def = TOOLS_SCHEMA.get(tool_name)
        if not tool_def:
            return {"type": "text", "content": f"未知工具：{tool_name}"}

        try:
            return tool_def["func"](**args)
        except TypeError as e:
            logger.error(f"[UtilityService] 参数不匹配 {tool_name}: {e}")
            return {"type": "text", "content": f"工具参数错误：{e}"}
        except Exception as e:
            logger.error(f"[UtilityService] 执行 {tool_name} 失败: {e}")
            return {"type": "text", "content": f"工具执行失败：{e}"}

    def _select_tool(self, text: str) -> tuple:
        tools_desc = "\n".join(
            f"- {name}: {info['description']}"
            for name, info in TOOLS_SCHEMA.items()
        )
        prompt = UTILITY_SYSTEM_PROMPT.format(tools_desc=tools_desc)

        try:
            raw = ollama_client.chat(
                service="chat", model_key="intent",
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": text},
                ],
                format="json",
            )
            data = self._parse_json(raw)
            return data.get("tool", ""), data.get("args", {}) or {}
        except Exception as e:
            logger.error(f"[UtilityService] LLM 选择失败: {e}")
            return "", {}

    @staticmethod
    def _parse_json(raw: str) -> dict:
        raw = (raw or "").strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            l, r = raw.find("{"), raw.rfind("}")
            if l >= 0 and r > l:
                try:
                    return json.loads(raw[l:r + 1])
                except Exception:
                    pass
            return {}


utility_service = UtilityService()