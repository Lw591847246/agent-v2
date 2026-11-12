"""CustomAvatar Skill 测试。"""
from unittest.mock import MagicMock, patch

from app.contracts.context import RequestContext
from app.contracts.result import Result
from app.executor.executor import Executor
from app.models.attachment import Attachment
from app.registry.skill_registry import SkillRegistry
from app.scheduler.intent_router import RoutingResult
from app.scheduler.intents import Intent
from app.scheduler.router_scheduler import RouterScheduler
from app.skills.custom_avatar.service import parse_user_input
from app.skills.custom_avatar.skill import CustomAvatarSkill


def test_parse_user_input_with_quotes():
    prompt, speech = parse_user_input('让这张图挥手，说“大家好，我是 AI 数字人”')
    assert "挥手" in prompt
    assert "大家好" in speech


def test_parse_user_input_no_quotes():
    prompt, speech = parse_user_input("帮我做一个数字人视频")
    assert prompt  # 非空
    assert speech  # 非空


def test_execute_submits_async():
    skill = CustomAvatarSkill()
    with patch("app.skills.custom_avatar.skill.file_registry") as mock_fr, \
         patch("app.skills.custom_avatar.skill.custom_avatar_service") as mock_svc:
        mock_fr.get.return_value = {"path": "/tmp/img.png", "name": "a.png"}
        mock_svc.submit.return_value = "avatar_abc"

        r = skill.execute(
            image_file_id="img1", raw_text="让这张图说你好",
            session_id="s1", user_id=1,
        )
        assert r.task_id == "avatar_abc"
        assert r.parts[0]["card_type"] == "skill_progress"


def test_execute_missing_image():
    skill = CustomAvatarSkill()
    r = skill.execute(image_file_id="", raw_text="")
    assert "请上传" in r.as_text()


def test_execute_image_not_found():
    skill = CustomAvatarSkill()
    with patch("app.skills.custom_avatar.skill.file_registry") as mock_fr:
        mock_fr.get.return_value = None
        r = skill.execute(image_file_id="missing")
        assert "找不到" in r.as_text()


def test_router_selects():
    reg = SkillRegistry()
    reg.register(CustomAvatarSkill())
    router = MagicMock()
    router.route.return_value = RoutingResult(
        intent=Intent.CUSTOM_AVATAR, confidence=0.9, params={}, source="llm",
    )
    scheduler = RouterScheduler(router=router, skill_registry=reg)
    ctx = RequestContext(
        text="让这张图说你好", session_id="s1",
        attachments=[Attachment(file_id="img1", type="image", name="a.png")],
    )
    decision = scheduler.route(ctx)
    assert decision.skill.name == "generate_custom_avatar"
    assert decision.kwargs["image_file_id"] == "img1"
    assert decision.kwargs["user_text"] == "让这张图说你好"