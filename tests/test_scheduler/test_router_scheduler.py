# tests/test_scheduler/test_router_scheduler.py
from unittest.mock import MagicMock, patch

from app.contracts.context import RequestContext
from app.contracts.result import Result, text_part
from app.contracts.skill import BaseSkill
from app.registry.skill_registry import SkillRegistry
from app.scheduler.intent_router import RoutingResult
from app.scheduler.intents import Intent
from app.scheduler.router_scheduler import RouterScheduler


class ChatSkill(BaseSkill):
    name = "chat"
    description = "chat"
    schema = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
    intent = "chat"
    category = "general"

    def execute(self, text, **kw):
        return Result(parts=[text_part(f"echo:{text}")])


class TranslateSkill(BaseSkill):
    name = "translate_text"
    description = "translate"
    schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}, "target_lang": {"type": "string"}},
        "required": ["text", "target_lang"],
    }
    intent = "translation"
    category = "general"

    def execute(self, text, target_lang, **kw):
        return Result(parts=[text_part(f"{text}->{target_lang}")])


def _build_registry():
    reg = SkillRegistry()
    reg.register(ChatSkill())
    reg.register(TranslateSkill())
    return reg


def test_route_translation():
    reg = _build_registry()
    router = MagicMock()
    router.route.return_value = RoutingResult(
        intent=Intent.TRANSLATION, confidence=0.9,
        params={"target_lang": "英文"}, source="llm",
    )
    s = RouterScheduler(router=router, skill_registry=reg)
    ctx = RequestContext(text="翻译成英文 hello", session_id="s")
    decision = s.route(ctx)
    assert decision.skill.name == "translate_text"
    assert decision.kwargs["target_lang"] == "英文"


def test_route_fallback_to_chat():
    reg = _build_registry()
    router = MagicMock()
    router.route.return_value = RoutingResult(
        intent=Intent.PPT_GENERATION, confidence=0.9,  # 没有对应 Skill
        params={}, source="llm",
    )
    s = RouterScheduler(router=router, skill_registry=reg)
    ctx = RequestContext(text="hi", session_id="s")
    decision = s.route(ctx)
    assert decision.skill.name == "chat"


def test_route_params_missing_falls_back():
    reg = _build_registry()
    router = MagicMock()
    router.route.return_value = RoutingResult(
        intent=Intent.TRANSLATION, confidence=0.9,
        params={},  # 缺 target_lang
        source="llm",
    )
    s = RouterScheduler(router=router, skill_registry=reg)
    ctx = RequestContext(text="hello", session_id="s")
    decision = s.route(ctx)
    # 参数缺失导致 provider 抛错，降级 chat
    assert decision.skill.name == "chat"