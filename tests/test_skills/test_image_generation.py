"""ImageGeneration Skill 测试。"""
from unittest.mock import MagicMock, patch

from app.contracts.context import RequestContext
from app.contracts.result import Result
from app.executor.executor import Executor
from app.registry.skill_registry import SkillRegistry
from app.scheduler.intent_router import RoutingResult
from app.scheduler.intents import Intent
from app.scheduler.router_scheduler import RouterScheduler
from app.skills.image_generation.skill import ImageGenerationSkill


def test_execute_submits_async():
    skill = ImageGenerationSkill()
    with patch("app.skills.image_generation.skill.task_runner") as mock_runner:
        mock_runner.submit.return_value = "image_gen_abc"
        r = skill.execute(
            prompt="画一只猫", session_id="s1", user_id=1,
        )
        assert r.task_id == "image_gen_abc"
        assert r.parts[0]["type"] == "card"
        assert r.parts[0]["card_type"] == "skill_progress"
        assert r.parts[0]["status"] == "processing"
        mock_runner.submit.assert_called_once()


def test_run_async_success():
    skill = ImageGenerationSkill()
    with patch("app.skills.image_generation.skill.image_generation_service") as mock_gen, \
         patch("app.skills.image_generation.skill.save_image") as mock_save, \
         patch("app.skills.image_generation.skill.conversation_repo") as mock_conv, \
         patch("app.skills.image_generation.skill.task_runner") as mock_runner:
        mock_gen.generate.return_value = {"image_base64": "fake"}
        mock_save.return_value = {
            "filename": "a.png",
            "thumb_filename": "a_thumb.jpg",
            "image_path": "/tmp/a.png",
        }
        mock_conv.get_message_id_by_task_id.return_value = 42

        skill._run_async("tid", "画猫", None, None, "s1", 1)

        # 验证 finish 被调用
        mock_runner.finish.assert_called_once()
        kwargs = mock_runner.finish.call_args[1]
        assert kwargs["message_extra"]["image_filename"] == "a.png"
        assert kwargs["message_extra"]["message_type"] == "image"
        assert kwargs["parts"][0]["type"] == "image"
        assert "/agent/v1/image/42" in kwargs["parts"][0]["url"]
        assert kwargs["outputs"][0]["kind"] == "image"


def test_run_async_generate_fail():
    skill = ImageGenerationSkill()
    with patch("app.skills.image_generation.skill.image_generation_service") as mock_gen, \
         patch("app.skills.image_generation.skill.task_runner") as mock_runner:
        mock_gen.generate.side_effect = RuntimeError("ComfyUI down")
        skill._run_async("tid", "画猫", None, None, "s1", 1)
        mock_runner.fail.assert_called_once()


def test_executor_missing_prompt():
    skill = ImageGenerationSkill()
    r = Executor().run(skill, {})
    assert "缺少必填参数" in r.as_text()


def test_router_selects_generation():
    reg = SkillRegistry()
    reg.register(ImageGenerationSkill())
    router = MagicMock()
    router.route.return_value = RoutingResult(
        intent=Intent.IMAGE_GENERATION, confidence=0.9, params={}, source="llm",
    )
    scheduler = RouterScheduler(router=router, skill_registry=reg)
    ctx = RequestContext(text="画一只猫", session_id="s1", user_id=1)
    decision = scheduler.route(ctx)
    assert decision.skill.name == "generate_image"