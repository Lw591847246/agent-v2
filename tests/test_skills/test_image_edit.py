"""ImageEdit Skill 测试。"""
from unittest.mock import MagicMock, patch

from app.contracts.context import RequestContext
from app.contracts.result import Result
from app.executor.executor import Executor
from app.models.attachment import Attachment
from app.registry.skill_registry import SkillRegistry
from app.scheduler.intent_router import RoutingResult
from app.scheduler.intents import Intent
from app.scheduler.router_scheduler import RouterScheduler
from app.skills.image_edit.skill import ImageEditSkill


def test_execute_with_file_id():
    skill = ImageEditSkill()
    with patch("app.skills.image_edit.skill.image_edit_service") as mock_svc, \
         patch("app.skills.image_edit.skill.task_runner") as mock_runner:
        mock_svc.resolve_image_b64.return_value = "fakeb64"
        mock_runner.submit.return_value = "image_edit_abc"
        r = skill.execute(
            prompt="改成水彩", image_file_id="img1",
            session_id="s1", user_id=1,
        )
        assert r.task_id == "image_edit_abc"
        assert r.parts[0]["card_type"] == "skill_progress"


def test_execute_no_image():
    skill = ImageEditSkill()
    with patch("app.skills.image_edit.skill.image_edit_service") as mock_svc:
        mock_svc.resolve_image_b64.return_value = ""
        r = skill.execute(prompt="改一下", session_id="s1", user_id=1)
        assert "未找到" in r.as_text()


def test_run_async_success():
    skill = ImageEditSkill()
    with patch("app.skills.image_edit.skill.image_edit_service") as mock_svc, \
         patch("app.skills.image_edit.skill.save_image") as mock_save, \
         patch("app.skills.image_edit.skill.conversation_repo") as mock_conv, \
         patch("app.skills.image_edit.skill.task_runner") as mock_runner:
        import base64
        mock_svc.edit.return_value = {"image_base64": base64.b64encode(b"fake").decode()}
        mock_save.return_value = {
            "filename": "b.png",
            "thumb_filename": "b_thumb.jpg",
            "image_path": "/tmp/b.png",
        }
        mock_conv.get_message_id_by_task_id.return_value = 43

        skill._run_async("tid", "改", base64.b64encode(b"orig").decode(), 0.8, "s1", 1)

        mock_runner.finish.assert_called_once()
        kwargs = mock_runner.finish.call_args[1]
        assert kwargs["message_extra"]["message_type"] == "image"
        assert kwargs["outputs"][0]["filename"] == "b.png"


def test_executor_missing_prompt():
    skill = ImageEditSkill()
    r = Executor().run(skill, {})
    assert "缺少必填参数" in r.as_text()


def test_router_selects_with_attachment():
    reg = SkillRegistry()
    reg.register(ImageEditSkill())
    router = MagicMock()
    router.route.return_value = RoutingResult(
        intent=Intent.IMAGE_EDIT, confidence=0.9, params={}, source="llm",
    )
    scheduler = RouterScheduler(router=router, skill_registry=reg)
    ctx = RequestContext(
        text="把这张图改成水彩", session_id="s1",
        attachments=[Attachment(file_id="img1", type="image", name="a.png")],
    )
    decision = scheduler.route(ctx)
    assert decision.skill.name == "edit_image"
    assert decision.kwargs["image_file_id"] == "img1"
    assert decision.kwargs["prompt"] == "把这张图改成水彩"