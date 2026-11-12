"""Utility Skill 测试。"""
from unittest.mock import MagicMock, patch

from app.contracts.context import RequestContext
from app.contracts.result import Result
from app.executor.executor import Executor
from app.registry.skill_registry import SkillRegistry
from app.scheduler.intent_router import RoutingResult
from app.scheduler.intents import Intent
from app.scheduler.router_scheduler import RouterScheduler
from app.skills.utility.skill import UtilitySkill
from app.skills.utility.tools import (
    tool_calculator, tool_date_diff, tool_datetime_now,
    tool_percentage, tool_temperature_convert, tool_unit_convert,
)


def test_tool_datetime():
    r = tool_datetime_now("date")
    assert "今天是" in r["content"]


def test_tool_calculator():
    r = tool_calculator("123*456")
    assert "56088" in r["content"]


def test_tool_calculator_bad():
    r = tool_calculator("__import__('os')")
    assert "失败" in r["content"]


def test_tool_percentage():
    r = tool_percentage(20, 100)
    assert "20" in r["content"]


def test_tool_unit_convert():
    r = tool_unit_convert(100, "公斤", "斤")
    assert "200" in r["content"]


def test_tool_temperature():
    r = tool_temperature_convert(50, "celsius", "fahrenheit")
    assert "122" in r["content"]


def test_tool_date_diff():
    r = tool_date_diff("2026-01-01", "2026-01-11")
    assert "10" in r["content"]


def test_execute_ok():
    skill = UtilitySkill()
    with patch("app.skills.utility.skill.utility_service") as mock_svc:
        mock_svc.handle.return_value = {"type": "text", "content": "今天是 2026年11月13日"}
        r = skill.execute(text="今天几号")
        assert "今天是" in r.as_text()


def test_executor_missing_text():
    skill = UtilitySkill()
    r = Executor().run(skill, {})
    assert "缺少必填参数" in r.as_text()


def test_router_selects():
    reg = SkillRegistry()
    reg.register(UtilitySkill())
    router = MagicMock()
    router.route.return_value = RoutingResult(
        intent=Intent.UTILITY, confidence=0.9, params={}, source="llm",
    )
    scheduler = RouterScheduler(router=router, skill_registry=reg)
    ctx = RequestContext(text="今天几号", session_id="s1", user_id=1)
    decision = scheduler.route(ctx)
    assert decision.skill.name == "utility"