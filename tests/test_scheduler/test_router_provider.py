import pytest

from app.contracts.context import RequestContext
from app.contracts.errors import ValidationError
from app.contracts.result import Result, text_part
from app.contracts.skill import BaseSkill
from app.models.attachment import Attachment
from app.scheduler.router_provider import RouterParameterProvider


class TranslateSkill(BaseSkill):
    name = "translate_text"
    description = "translate"
    schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "target_lang": {"type": "string"},
        },
        "required": ["text", "target_lang"],
    }
    intent = "translation"
    category = "general"

    def execute(self, text, target_lang, **kw):
        return Result(parts=[text_part(f"{text}->{target_lang}")])


class ImageUnderstandSkill(BaseSkill):
    name = "image_understand"
    description = "understand"
    schema = {
        "type": "object",
        "properties": {
            "file_id": {"type": "string"},
            "question": {"type": "string"},
        },
        "required": ["file_id"],
    }
    intent = "image_understanding"
    category = "image"
    requires_attachments = ["image"]

    def execute(self, file_id, question="", **kw):
        return Result(parts=[text_part(f"{file_id}: {question}")])


def test_text_and_l2_params():
    p = RouterParameterProvider()
    ctx = RequestContext(text="翻译成英文 hello", session_id="s", user_id=1)
    skill = TranslateSkill()
    kwargs = p.provide(ctx, skill, {"target_lang": "英文"})
    assert kwargs["text"] == "翻译成英文 hello"
    assert kwargs["target_lang"] == "英文"


def test_missing_required_raises():
    p = RouterParameterProvider()
    ctx = RequestContext(text="hi", session_id="s")
    skill = TranslateSkill()
    with pytest.raises(ValidationError):
        p.provide(ctx, skill, {})


def test_file_id_from_attachment():
    p = RouterParameterProvider()
    ctx = RequestContext(
        text="描述这张图",
        session_id="s",
        attachments=[Attachment(file_id="img1", type="image", name="a.png")],
    )
    skill = ImageUnderstandSkill()
    kwargs = p.provide(ctx, skill, {})
    assert kwargs["file_id"] == "img1"
    assert kwargs["question"] == "描述这张图"