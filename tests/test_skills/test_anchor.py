"""Anchor Skill 测试。"""
from unittest.mock import MagicMock, patch

from app.contracts.context import RequestContext
from app.contracts.result import Result
from app.executor.executor import Executor
from app.registry.skill_registry import SkillRegistry
from app.scheduler.intent_router import RoutingResult
from app.scheduler.intents import Intent
from app.scheduler.router_scheduler import RouterScheduler
from app.skills.anchor.service import extract_speech_text
from app.skills.anchor.skill import AnchorSkill


def test_extract_speech_text_with_quotes():
    raw = "用孔子画像讲一段“学而时习之，不亦说乎”"
    result = extract_speech_text(raw)
    assert "学而时习之" in result


def test_extract_speech_text_no_quotes():
    raw = "帮我讲一段论语"
    result = extract_speech_text(raw)
    assert result  # 非空


def test_execute_submits_async():
    skill = AnchorSkill()
    with patch("app.skills.anchor.skill.anchor_service") as mock_svc:
        mock_svc.submit.return_value = "anchor_abc"
        r = skill.execute(
            speech_text="学而时习之", session_id="s1", user_id=1,
        )
        assert r.task_id == "anchor_abc"
        assert r.parts[0]["card_type"] == "skill_progress"


def test_execute_empty():
    skill = AnchorSkill()
    r = skill.execute(speech_text="", user_text="", session_id="s1")
    assert "请提供" in r.as_text()


def test_run_pipeline_success():
    from app.skills.anchor.service import AnchorService
    svc = AnchorService()
    with patch("app.skills.anchor.service.tts_service") as mock_tts, \
         patch("app.skills.anchor.service.requests") as mock_requests, \
         patch("app.skills.anchor.service.task_runner") as mock_runner:
        mock_tts.synthesize_wav.return_value = "/tmp/a.wav"

        post_resp = MagicMock(status_code=200)
        post_resp.json.return_value = {"job_id": "lp_job_1"}
        mock_requests.post.return_value = post_resp

        get_resp = MagicMock(status_code=200)
        get_resp.json.return_value = {
            "status": "done", "progress": 100, "output_path": "/tmp/lp.mp4",
        }
        mock_requests.get.return_value = get_resp

        svc._run_pipeline("tid", "s1", "学而时习之")

        mock_runner.finish.assert_called_once()
        kwargs = mock_runner.finish.call_args[1]
        assert kwargs["outputs"][0]["kind"] == "video"


def test_run_pipeline_failure():
    from app.skills.anchor.service import AnchorService
    svc = AnchorService()
    with patch("app.skills.anchor.service.tts_service") as mock_tts, \
         patch("app.skills.anchor.service.task_runner") as mock_runner:
        mock_tts.synthesize_wav.side_effect = RuntimeError("TTS down")
        svc._run_pipeline("tid", "s1", "学而时习之")
        mock_runner.fail.assert_called_once()


def test_router_selects():
    reg = SkillRegistry()
    reg.register(AnchorSkill())
    router = MagicMock()
    router.route.return_value = RoutingResult(
        intent=Intent.PORTRAIT_ANIMATION, confidence=0.9, params={}, source="llm",
    )
    scheduler = RouterScheduler(router=router, skill_registry=reg)
    ctx = RequestContext(text="孔子讲一段论语", session_id="s1", user_id=1)
    decision = scheduler.route(ctx)
    assert decision.skill.name == "generate_standard_anchor"
    assert decision.kwargs.get("user_text") == "孔子讲一段论语"