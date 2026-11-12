"""TextToVideo Skill 测试。"""
from unittest.mock import MagicMock, patch

from app.contracts.context import RequestContext
from app.contracts.result import Result
from app.executor.executor import Executor
from app.registry.skill_registry import SkillRegistry
from app.scheduler.intent_router import RoutingResult
from app.scheduler.intents import Intent
from app.scheduler.router_scheduler import RouterScheduler
from app.skills.text_to_video.skill import TextToVideoSkill


def test_execute_submits_async():
    skill = TextToVideoSkill()
    with patch("app.skills.text_to_video.skill.task_runner") as mock_runner, \
         patch("app.skills.text_to_video.skill.text_to_video_service") as mock_svc:
        mock_svc.detect_preset.return_value = "standard"
        mock_svc.detect_duration.return_value = 3
        mock_svc.submit.return_value = {
            "prompt": "风景", "negative_prompt": "",
            "preset": "standard", "duration_seconds": 3, "length": 48,
        }
        mock_runner.submit.return_value = "t2v_abc"

        r = skill.execute(prompt="画一段风景视频", session_id="s1", user_id=1)
        assert r.task_id == "t2v_abc"
        assert r.parts[0]["card_type"] == "skill_progress"


def test_execute_empty_prompt():
    skill = TextToVideoSkill()
    r = skill.execute(prompt="")
    assert "缺少" in r.as_text()


def test_run_async_success():
    skill = TextToVideoSkill()
    with patch("app.skills.text_to_video.skill.text_to_video_service") as mock_svc, \
         patch("app.skills.text_to_video.skill.task_runner") as mock_runner:
        mock_svc.run_pipeline.return_value = "/tmp/video.mp4"

        skill._run_async("tid", "s1", 1, {
            "prompt": "风景", "negative_prompt": "",
            "preset": "standard", "length": 48,
        })

        mock_runner.finish.assert_called_once()
        kwargs = mock_runner.finish.call_args[1]
        assert kwargs["outputs"][0]["kind"] == "video"
        assert kwargs["parts"][0]["type"] == "video"


def test_run_async_failure():
    skill = TextToVideoSkill()
    with patch("app.skills.text_to_video.skill.text_to_video_service") as mock_svc, \
         patch("app.skills.text_to_video.skill.task_runner") as mock_runner:
        mock_svc.run_pipeline.side_effect = RuntimeError("ComfyUI down")
        skill._run_async("tid", "s1", 1, {"prompt": "风景", "negative_prompt": "",
                                         "preset": "standard", "length": None})
        mock_runner.fail.assert_called_once()


def test_executor_missing_prompt():
    skill = TextToVideoSkill()
    r = Executor().run(skill, {})
    assert "缺少必填参数" in r.as_text()


def test_router_selects():
    reg = SkillRegistry()
    reg.register(TextToVideoSkill())
    router = MagicMock()
    router.route.return_value = RoutingResult(
        intent=Intent.TEXT_TO_VIDEO, confidence=0.9, params={}, source="llm",
    )
    scheduler = RouterScheduler(router=router, skill_registry=reg)
    ctx = RequestContext(text="生成一段视频", session_id="s1", user_id=1)
    decision = scheduler.route(ctx)
    assert decision.skill.name == "text_to_video"