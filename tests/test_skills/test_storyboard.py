"""Storyboard Skill 测试。"""
from unittest.mock import MagicMock, patch

import pytest

from app.contracts.context import RequestContext
from app.contracts.result import Result
from app.executor.executor import Executor
from app.registry.skill_registry import SkillRegistry
from app.scheduler.intent_router import RoutingResult
from app.scheduler.intents import Intent
from app.scheduler.router_scheduler import RouterScheduler
from app.skills.storyboard.skill import StoryboardSkill


def test_execute_with_story():
    skill = StoryboardSkill()
    with patch("app.skills.storyboard.skill.storyboard_service") as mock_svc:
        mock_svc.submit.return_value = "storyboard_abc"
        r = skill.execute(
            story="从前有座山，" * 50, style="realistic_cinema",
            session_id="s1", user_id=1,
        )
        assert r.task_id == "storyboard_abc"
        assert r.parts[0]["card_type"] == "skill_progress"


def test_execute_short_story():
    skill = StoryboardSkill()
    r = skill.execute(story="太短", session_id="s1")
    assert "太短" in r.as_text()


def test_execute_no_story_no_history():
    skill = StoryboardSkill()
    with patch("app.skills.storyboard.skill.extract_story", return_value=("", "empty")):
        r = skill.execute(user_text="做成视频", history=[], session_id="s1")
        assert "未找到故事" in r.as_text()


def test_execute_extracts_from_history():
    skill = StoryboardSkill()
    with patch("app.skills.storyboard.skill.extract_story",
               return_value=("从前有座山，" * 50, "history")), \
         patch("app.skills.storyboard.skill.storyboard_service") as mock_svc:
        mock_svc.submit.return_value = "storyboard_xyz"
        r = skill.execute(user_text="把上一轮的故事做成视频", history=[], session_id="s1")
        assert r.task_id == "storyboard_xyz"


def test_executor_missing_story_falls_back():
    """story 不再 required，Executor 会调 execute，execute 内部返回'未找到故事'。"""
    skill = StoryboardSkill()
    with patch("app.skills.storyboard.skill.extract_story", return_value=("", "empty")):
        r = Executor().run(skill, {})
        assert "未找到故事" in r.as_text()


def test_router_selects():
    reg = SkillRegistry()
    reg.register(StoryboardSkill())
    router = MagicMock()
    router.route.return_value = RoutingResult(
        intent=Intent.STORYBOARD, confidence=0.9, params={}, source="llm",
    )
    scheduler = RouterScheduler(router=router, skill_registry=reg)
    ctx = RequestContext(text="做成漫剧", session_id="s1", user_id=1)
    decision = scheduler.route(ctx)
    assert decision.skill.name == "generate_storyboard"
    # user_text 被自动填
    assert decision.kwargs.get("user_text") == "做成漫剧"


def test_regenerate_manifest_not_found():
    from app.skills.storyboard.service import storyboard_service
    with pytest.raises(FileNotFoundError):
        storyboard_service.regenerate("nonexistent_task_xyz", "")