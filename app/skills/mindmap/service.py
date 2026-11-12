# app/skills/mindmap/service.py
"""思维导图业务：LLM 出树 → 生成 Markdown 文件。"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional

from app.core.logger import logger
from app.infrastructure.clients.model_manager import model_manager
from app.skills.mindmap.prompts import PROMPT


OUTPUT_ROOT = "./data/mindmap_output"


def _sanitize_filename(name: str, default: str = "mindmap") -> str:
    if not name:
        return default
    name = name.replace("/", "_").replace("\\", "_").replace("..", "")
    name = "".join(c for c in name if c.isalnum() or c in "-_ 中文")
    name = name.strip().replace(" ", "_")[:40]
    return name or default


def _tree_to_markdown(tree: Dict, level: int = 0) -> str:
    """递归把树转成 Markdown 列表。"""
    if not isinstance(tree, dict):
        return ""
    name = tree.get("name", "")
    if not name:
        return ""
    indent = "  " * level
    if level == 0:
        line = f"# {name}\n\n"
    else:
        line = f"{indent}- {name}\n"
    children = tree.get("children") or []
    for child in children:
        line += _tree_to_markdown(child, level + 1)
    if level == 0:
        line += "\n"
    return line


class MindmapService:

    def generate(
        self, topic: str, text: str = "", session_id: str = "",
    ) -> dict:
        """
        返回 {"type": "text", "content": "..."} 或
             {"type": "file", "filename": "...", "path": "...", "url": "...", "title": "..."}
        """
        user_msg = self._build_user_msg(topic, text)

        try:
            raw = model_manager.chat(
                task_type="chat",
                messages=[
                    {"role": "system", "content": PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                format="json",
            )
        except Exception as e:
            logger.error(f"[Mindmap] LLM 调用失败: {e}")
            return {"type": "text", "content": f"思维导图生成失败：{e}"}

        data = self._parse_json(raw)
        if not data or not data.get("tree"):
            return {"type": "text", "content": "思维导图生成失败：LLM 返回格式异常"}

        title = data.get("title") or topic or "思维导图"
        tree = data["tree"]

        # 生成 Markdown
        markdown = _tree_to_markdown(tree)
        if not markdown.strip():
            return {"type": "text", "content": "思维导图生成失败：树结构为空"}

        # 写文件
        safe_title = _sanitize_filename(title, "mindmap")
        session_dir = os.path.join(OUTPUT_ROOT, session_id or "default")
        os.makedirs(session_dir, exist_ok=True)
        filename = f"{safe_title}_{int(time.time())}.md"
        abs_path = os.path.join(session_dir, filename)

        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(markdown)

        rel_path = abs_path.replace("\\", "/")
        url = f"/agent/v1/skill/download?path={rel_path}"

        logger.info(f"[Mindmap] 生成完成: {abs_path}")
        return {
            "type": "file",
            "title": title,
            "filename": filename,
            "path": abs_path,
            "url": url,
            "markdown": markdown,
        }

    @staticmethod
    def _build_user_msg(topic: str, text: str = "") -> str:
        if text and text.strip():
            return f"请为以下内容生成思维导图：\n\n{topic}\n\n{text}"
        return f"请为「{topic}」生成思维导图"

    @staticmethod
    def _parse_json(raw: str) -> dict:
        raw = (raw or "").strip()
        if raw.startswith("```json"):
            raw = raw[7:-3]
        elif raw.startswith("```"):
            raw = raw[3:-3]
        raw = raw.strip()

        l = raw.find("{")
        if l >= 0:
            raw = raw[l:]

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            try:
                from json_repair import repair_json
                return json.loads(repair_json(raw))
            except Exception:
                return {}


mindmap_service = MindmapService()