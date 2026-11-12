# app/skills/mindmap/skill.py
"""Mindmap Skill：思维导图生成。"""
from __future__ import annotations

from app.contracts.result import (
    Result, build_output, card_part, text_part,
)
from app.contracts.skill import BaseSkill
from app.skills.mindmap.service import mindmap_service


class MindmapSkill(BaseSkill):
    name = "generate_mindmap"
    description = (
        "根据主题或内容生成思维导图（Markdown 文件）。"
        "适用于：'思维导图/脑图/知识树/梳理XX结构'。"
        "不适用于：PPT（用 generate_ppt）、Word（用 generate_word）、产品原型（用 prototype）。"
    )
    schema = {
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "思维导图主题"},
            "text": {"type": "string", "description": "补充内容，可空"},
        },
        "required": ["topic"],
    }
    intent = "mindmap"
    category = "content"
    sync = True
    returns = ["card", "file_download"]
    requires_attachments = []
    produces_outputs = True
    visible_to_l2 = True
    visible_to_planner = True

    def execute(
        self, topic: str = "", text: str = "",
        session_id: str = "", user_id: int = 0, **kwargs,
    ) -> Result:
        if not topic or not topic.strip():
            return Result(parts=[text_part("请告诉我思维导图的主题")])

        info = mindmap_service.generate(
            topic=topic, text=text, session_id=session_id,
        )

        if info["type"] == "text":
            return Result(parts=[text_part(info["content"])])

        # 成功：返回卡片 + 预览文本
        title = info["title"]
        filename = info["filename"]
        download_url = info["url"]

        result_data = {
            "card_type": "file_download",
            "title": f"思维导图：{title}",
            "filename": filename,
            "desc": "Markdown 格式",
            "download_url": download_url,
        }
        card = card_part("file_download", result_data, status="done")
        preview = text_part(info.get("markdown", "")[:800] + "\n\n（完整内容请下载）")

        output = build_output(
            kind="file",
            filename=filename,
            path=info["path"],
            url=download_url,
        )
        return Result(parts=[card, preview], outputs=[output])


skill = MindmapSkill()