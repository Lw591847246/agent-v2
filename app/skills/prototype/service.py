# app/skills/prototype/service.py
"""产品原型业务：LLM 出 HTML → 保存文件。"""
from __future__ import annotations

import os
import re
import time

from app.core.logger import logger
from app.infrastructure.clients.model_manager import model_manager
from app.skills.prototype.prompts import PROMPT


OUTPUT_ROOT = "./data/prototype_output"


def _sanitize_filename(name: str, default: str = "prototype") -> str:
    if not name:
        return default
    name = name.replace("/", "_").replace("\\", "_").replace("..", "")
    name = "".join(c for c in name if c.isalnum() or c in "-_ 中文")
    name = name.strip().replace(" ", "_")[:40]
    return name or default


def _extract_html(raw: str) -> str:
    """从 LLM 输出里提取 HTML 正文（去掉 markdown 包裹和多余解释）。"""
    if not raw:
        return ""
    raw = raw.strip()
    # 去 markdown 代码块
    if raw.startswith("```html"):
        raw = raw[7:]
    elif raw.startswith("```"):
        raw = raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()

    # 找第一个 <!DOCTYPE 或 <html
    m = re.search(r"(<!DOCTYPE\s+html|<html)", raw, re.IGNORECASE)
    if m:
        raw = raw[m.start():]

    # 找最后一个 </html>
    m_end = raw.lower().rfind("</html>")
    if m_end >= 0:
        raw = raw[:m_end + 7]

    return raw.strip()


class PrototypeService:

    def generate(
        self, requirement: str, title: str = "", session_id: str = "",
    ) -> dict:
        """
        返回 {"type": "text", "content": "..."} 或
             {"type": "file", "filename": "...", "path": "...", "url": "...", "title": "..."}
        """
        user_msg = self._build_user_msg(requirement)

        try:
            raw = model_manager.chat(
                task_type="chat",
                messages=[
                    {"role": "system", "content": PROMPT},
                    {"role": "user", "content": user_msg},
                ],
            )
        except Exception as e:
            logger.error(f"[Prototype] LLM 调用失败: {e}")
            return {"type": "text", "content": f"原型生成失败：{e}"}

        html = _extract_html(raw)
        if not html or "<body" not in html.lower():
            return {"type": "text", "content": "原型生成失败：LLM 未返回有效 HTML"}

        display_title = title or (requirement[:30] + "..." if len(requirement) > 30 else requirement) or "产品原型"
        safe_title = _sanitize_filename(display_title, "prototype")

        session_dir = os.path.join(OUTPUT_ROOT, session_id or "default")
        os.makedirs(session_dir, exist_ok=True)
        filename = f"{safe_title}_{int(time.time())}.html"
        abs_path = os.path.join(session_dir, filename)

        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(html)

        rel_path = abs_path.replace("\\", "/")
        url = f"/agent/v1/skill/download?path={rel_path}"

        logger.info(f"[Prototype] 生成完成: {abs_path} ({len(html)} bytes)")
        return {
            "type": "file",
            "title": display_title,
            "filename": filename,
            "path": abs_path,
            "url": url,
            "html": html,
        }

    @staticmethod
    def _build_user_msg(requirement: str) -> str:
        return f"请根据以下需求生成产品原型：\n\n{requirement}"


prototype_service = PrototypeService()