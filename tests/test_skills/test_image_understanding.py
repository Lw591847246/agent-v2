# tests/test_skills/test_image_understanding.py
"""ImageUnderstanding Skill 测试。"""
from unittest.mock import MagicMock, patch

from app.contracts.context import RequestContext
from app.contracts.result import Result
from app.executor.executor import Executor
from app.models.attachment import Attachment
from app.registry.skill_registry import SkillRegistry
from app.scheduler.intent_router import RoutingResult
from app.scheduler.intents import Intent
from app.scheduler.router_scheduler import RouterScheduler
from app.skills.image_understanding.skill import ImageUnderstandingSkill


def _patch_image(service_module="app.skills.image_understanding.service"):
    """patch 读图 + VLM 调用。"""
    return (
        patch(f"{service_module}.ImageUnderstandingService._load_image_b64", return_value="fakeb64"),
        patch(f"{service_module}.ImageUnderstandingService._call_vlm", return_value="一只猫"),
    )


def test_execute_describe():
    skill = ImageUnderstandingSkill()
    p1, p2 = _patch_image()
    with p1, p2 as mock_vlm:
        r = skill.execute(file_id="img1", question="这是什么")
        assert isinstance(r, Result)
        assert r.as_text() == "一只猫"
        # describe 模式
        prompt = mock_vlm.call_args[0][0]
        assert "这是什么" in prompt


def test_execute_ocr_mode():
    skill = ImageUnderstandingSkill()
    p1, p2 = _patch_image()
    with p1, p2 as mock_vlm:
        r = skill.execute(file_id="img1", question="提取文字")
        assert r.as_text() == "一只猫"
        # OCR 模式 prompt
        prompt = mock_vlm.call_args[0][0]
        assert "文字" in prompt
        # ocr 模式 model_key 应该是 ocr
        assert mock_vlm.call_args[0][2] == "ocr"


def test_execute_solve_mode():
    skill = ImageUnderstandingSkill()
    p1, p2 = _patch_image()
    with p1, p2 as mock_vlm:
        skill.execute(file_id="img1", question="这道题怎么解")
        prompt = mock_vlm.call_args[0][0]
        assert "推导" in prompt or "答案" in prompt


def test_execute_image_not_found():
    skill = ImageUnderstandingSkill()
    with patch(
        "app.skills.image_understanding.service.ImageUnderstandingService._load_image_b64",
        return_value="",
    ):
        r = skill.execute(file_id="missing")
        assert "不存在" in r.as_text() or "过期" in r.as_text()


def test_executor_missing_file_id():
    skill = ImageUnderstandingSkill()
    r = Executor().run(skill, {})
    assert "缺少必填参数" in r.as_text()


def test_router_selects_with_attachment():
    reg = SkillRegistry()
    reg.register(ImageUnderstandingSkill())
    router = MagicMock()
    router.route.return_value = RoutingResult(
        intent=Intent.IMAGE_UNDERSTANDING, confidence=0.9,
        params={}, source="llm",
    )
    scheduler = RouterScheduler(router=router, skill_registry=reg)
    ctx = RequestContext(
        text="描述这张图", session_id="s1",
        attachments=[Attachment(file_id="img1", type="image", name="a.png")],
    )
    decision = scheduler.route(ctx)
    assert decision.skill.name == "image_understand"
    assert decision.kwargs["file_id"] == "img1"
    assert decision.kwargs["question"] == "描述这张图"