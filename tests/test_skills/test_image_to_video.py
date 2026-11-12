"""ImageToVideo Skill 测试。"""
from unittest.mock import MagicMock, patch

from app.contracts.context import RequestContext
from app.contracts.result import Result
from app.executor.executor import Executor
from app.models.attachment import Attachment
from app.registry.skill_registry import SkillRegistry
from app.scheduler.intent_router import RoutingResult
from app.scheduler.intents import Intent
from app.scheduler.router_scheduler import RouterScheduler
from app.skills.image_to_video.skill import ImageToVideoSkill


def test_execute_submits_async():
    skill = ImageToVideoSkill()
    with patch("app.skills.image_to_video.skill.task_runner") as mock_runner, \
         patch("app.skills.image_to_video.skill.image_to_video_service") as mock_svc:
        mock_svc.split_text.return_value = ("主播说话", "", False)
        mock_svc.detect_duration.return_value = 5
        mock_svc.detect_voice.return_value = "zf_xiaoxiao"
        mock_svc.detect_preset.return_value = "standard"
        mock_svc.clean_i2v_prompt.return_value = "主播说话"
        mock_svc.truncate_speech.return_value = ""
        mock_runner.submit.return_value = "i2v_abc"

        r = skill.execute(
            image_file_id="img1", prompt="让主播说话",
            session_id="s1", user_id=1,
        )
        assert r.task_id == "i2v_abc"
        assert r.parts[0]["card_type"] == "skill_progress"


def test_execute_missing_image():
    skill = ImageToVideoSkill()
    r = skill.execute(image_file_id="", prompt="让它动")
    assert "未检测到图片" in r.as_text()


def test_run_async_success_no_speech():
    skill = ImageToVideoSkill()
    with patch("app.skills.image_to_video.skill.image_to_video_service") as mock_svc, \
         patch("app.skills.image_to_video.skill.task_runner") as mock_runner:
        mock_svc.run_pipeline.return_value = "/tmp/v.mp4"
        skill._run_async("tid", "s1", 1, "img1", "让它动", "standard", 5, "", "zf_xiaoxiao")

        mock_runner.finish.assert_called_once()
        kwargs = mock_runner.finish.call_args[1]
        assert kwargs["outputs"][0]["kind"] == "video"
        assert "无声" in kwargs["parts"][1]["content"]


def test_run_async_with_speech():
    skill = ImageToVideoSkill()
    with patch("app.skills.image_to_video.skill.image_to_video_service") as mock_svc, \
         patch("app.skills.image_to_video.skill.task_runner") as mock_runner:
        mock_svc.run_pipeline.return_value = "/tmp/v.mp4"
        skill._run_async("tid", "s1", 1, "img1", "让它说话", "standard", 5, "你好世界", "zf_xiaoxiao")

        mock_runner.finish.assert_called_once()
        kwargs = mock_runner.finish.call_args[1]
        assert "包含配音" in kwargs["parts"][1]["content"]


def test_run_async_failure():
    skill = ImageToVideoSkill()
    with patch("app.skills.image_to_video.skill.image_to_video_service") as mock_svc, \
         patch("app.skills.image_to_video.skill.task_runner") as mock_runner:
        mock_svc.run_pipeline.side_effect = RuntimeError("I2V failed")
        skill._run_async("tid", "s1", 1, "img1", "动", "standard", 5, "", "zf_xiaoxiao")
        mock_runner.fail.assert_called_once()


def test_executor_missing_image_file_id():
    skill = ImageToVideoSkill()
    r = Executor().run(skill, {"prompt": "让它动"})
    assert "缺少必填参数" in r.as_text()


def test_router_selects():
    reg = SkillRegistry()
    reg.register(ImageToVideoSkill())
    router = MagicMock()
    router.route.return_value = RoutingResult(
        intent=Intent.IMAGE_TO_VIDEO, confidence=0.9, params={}, source="llm",
    )
    scheduler = RouterScheduler(router=router, skill_registry=reg)
    ctx = RequestContext(
        text="让这张图动起来", session_id="s1",
        attachments=[Attachment(file_id="img1", type="image", name="a.png")],
    )
    decision = scheduler.route(ctx)
    assert decision.skill.name == "image_to_video"
    assert decision.kwargs["image_file_id"] == "img1"