# app/skills/web_search/skill.py
"""WebSearch Skill：联网搜索 + 答案整合。"""
from __future__ import annotations

from typing import Dict, Iterator, List

from app.contracts.result import Result, text_part
from app.contracts.skill import BaseSkill
from app.skills.web_search.service import web_search_service


class WebSearchSkill(BaseSkill):
    name = "web_search"
    description = (
        "联网搜索实时信息并整合成答案。适用于：新闻、股价、汇率、天气、比分、"
        "近期事件、某公司/某人的最新动态、最新发布等需要实时数据的场景。"
        "不适用于：闲聊、常识问答、写故事、写代码、翻译、纯数学推理、"
        "日期时间查询、数学计算、单位换算（这些用 utility 工具）。"
    )
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词或问题"},
            "history": {"type": "array", "description": "历史对话，可选"},
        },
        "required": ["query"],
    }
    intent = "web_search"
    category = "general"
    sync = True
    returns = ["text"]
    requires_attachments = []
    produces_outputs = False
    visible_to_l2 = True
    visible_to_planner = True

    def execute(
        self, query: str = "", history: List[Dict] = None, **kwargs,
    ) -> Result:
        # 兼容 text 字段
        if not query:
            query = kwargs.get("text") or ""
        if not query.strip():
            return Result(parts=[text_part("请告诉我想搜索什么。")])
        answer = web_search_service.chat(query, history or [])
        return Result(parts=[text_part(answer)])

    def stream(
        self, query: str = "", history: List[Dict] = None, **kwargs,
    ) -> Iterator[str]:
        if not query:
            query = kwargs.get("text") or ""
        if not query.strip():
            yield "请告诉我想搜索什么。"
            return
        yield from web_search_service.chat_stream(query, history or [])


skill = WebSearchSkill()