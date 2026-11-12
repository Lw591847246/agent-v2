# app/skills/prototype/skill.py
"""Prototype Skill：产品原型生成。"""
from __future__ import annotations

from app.contracts.result import (
    Result, build_output, card_part, text_part,
)
from app.contracts.skill import BaseSkill
from app.skills.prototype.service import prototype_service


class PrototypeSkill(BaseSkill):
    name = "generate_prototype"
    description = (
        "根据需求生成单页 HTML 产品原型（网页设计稿）。"
        "适用于：'产品原型/网页原型/HTML设计/页面设计/做一个页面'。"
        "不适用于：PPT（用 generate_ppt）、思维导图（用 generate_mindmap）、完整网站代码（用 code_generate）。"
    )
    schema = {
        "type": "object",
        "properties": {
            "requirement": {"type": "string", "description": "产品需求描述"},
            "title": {"type": "string", "description": "页面标题，可空"},
        },
        "required": ["requirement"],
    }
    intent = "prototype"
    category = "content"
    sync = True
    returns = ["card", "file_download"]
    requires_attachments = []
    produces_outputs = True
    visible_to_l2 = True
    visible_to_planner = True

    def execute(
        self, requirement: str = "", title: str = "",
        session_id: str = "", user_id: int = 0, **kwargs,
    ) -> Result:
        if not requirement or not requirement.strip():
            return Result(parts=[text_part("请描述你想要的产品原型")])

        info = prototype_service.generate(
            requirement=requirement, title=title, session_id=session_id,
        )

        if info["type"] == "text":
            return Result(parts=[text_part(info["content"])])

        display_title = info["title"]
        filename = info["filename"]
        download_url = info["url"]

        result_data = {
            "card_type": "file_download",
            "title": f"产品原型：{display_title}",
            "filename": filename,
            "desc": "HTML 格式，可下载后在浏览器打开",
            "download_url": download_url,
            "preview_url": download_url,
        }
        card = card_part("file_download", result_data, status="done")

        output = build_output(
            kind="file",
            filename=filename,
            path=info["path"],
            url=download_url,
        )
        return Result(parts=[card], outputs=[output])


skill = PrototypeSkill()