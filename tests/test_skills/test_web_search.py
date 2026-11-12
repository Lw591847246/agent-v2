"""WebSearch Skill 测试。"""
from unittest.mock import MagicMock, patch

from app.contracts.context import RequestContext
from app.contracts.result import Result, StreamResult
from app.executor.executor import Executor
from app.registry.skill_registry import SkillRegistry
from app.scheduler.intent_router import RoutingResult
from app.scheduler.intents import Intent
from app.scheduler.router_scheduler import RouterScheduler
from app.skills.web_search.skill import WebSearchSkill


def test_execute_ok():
    skill = WebSearchSkill()
    with patch("app.skills.web_search.skill.web_search_service") as mock_svc:
        mock_svc.chat.return_value = "今天南京多云，20-28度"
        r = skill.execute(query="今天南京天气")
        assert "南京" in r.as_text()


def test_stream_yields():
    skill = WebSearchSkill()
    with patch("app.skills.web_search.skill.web_search_service") as mock_svc:
        mock_svc.chat_stream.return_value = iter(["今天", "多云"])
        chunks = list(skill.stream(query="天气"))
        assert chunks == ["今天", "多云"]


def test_execute_empty():
    skill = WebSearchSkill()
    r = skill.execute(query="")
    assert "请告诉我" in r.as_text()


def test_executor_missing_query():
    skill = WebSearchSkill()
    r = Executor().run(skill, {})
    assert "缺少必填参数" in r.as_text()


def test_router_selects():
    reg = SkillRegistry()
    reg.register(WebSearchSkill())
    router = MagicMock()
    router.route.return_value = RoutingResult(
        intent=Intent.WEB_SEARCH, confidence=0.9, params={}, source="llm",
    )
    scheduler = RouterScheduler(router=router, skill_registry=reg)
    ctx = RequestContext(text="今天南京天气", session_id="s1", user_id=1)
    decision = scheduler.route(ctx)
    assert decision.skill.name == "web_search"