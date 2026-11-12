"""Ppt Skill 测试。"""
from unittest.mock import MagicMock, patch

from app.contracts.context import RequestContext
from app.contracts.result import Result
from app.executor.executor import Executor
from app.registry.skill_registry import SkillRegistry
from app.scheduler.intent_router import RoutingResult
from app.scheduler.intents import Intent
from app.scheduler.router_scheduler import RouterScheduler
from app.skills.ppt.skill import PptSkill


def test_execute_submits_async():
    skill = PptSkill()
    with patch("app.skills.ppt.skill.ppt_service") as mock_svc:
        mock_svc.submit.return_value = "ppt_abc"
        r = skill.execute(content="讲 AI 架构", session_id="s1", user_id=1)
        assert r.task_id == "ppt_abc"
        assert r.parts[0]["type"] == "card"
        assert r.parts[0]["card_type"] == "skill_progress"


def test_execute_empty_content():
    skill = PptSkill()
    r = skill.execute(content="")
    assert "缺少" in r.as_text()


def test_run_async_success():
    from app.skills.ppt.service import PptService
    svc = PptService()
    with patch("app.skills.ppt.service.model_manager") as mock_llm, \
         patch("app.skills.ppt.service.dynamic_ppt_builder") as mock_builder, \
         patch("app.skills.ppt.service.conversation_repo") as mock_conv, \
         patch("app.skills.ppt.service.task_runner") as mock_runner:
        mock_llm.chat.return_value = '{"title": "测试", "slides": [{"type": "cover", "title": "封面"}]}'
        mock_builder.build.return_value = "s1/ppt_1.pptx"
        mock_builder.OUTPUT_ROOT = "./data/ppt_output"
        mock_conv.get_message_id_by_task_id.return_value = 50

        svc._run_async("tid", "内容", "s1")

        mock_runner.finish.assert_called_once()
        kwargs = mock_runner.finish.call_args[1]
        assert kwargs["outputs"][0]["kind"] == "file"
        assert kwargs["parts"][0]["card_type"] == "file_download"


def test_router_selects_ppt():
    reg = SkillRegistry()
    reg.register(PptSkill())
    router = MagicMock()
    router.route.return_value = RoutingResult(
        intent=Intent.PPT_GENERATION, confidence=0.9, params={}, source="llm",
    )
    scheduler = RouterScheduler(router=router, skill_registry=reg)
    ctx = RequestContext(text="帮我做PPT", session_id="s1", user_id=1)
    decision = scheduler.route(ctx)
    assert decision.skill.name == "generate_ppt"