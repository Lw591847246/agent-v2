# tests/test_skills/test_translation.py
"""Translation Skill 测试。"""
from unittest.mock import MagicMock, patch

from app.contracts.context import RequestContext
from app.contracts.result import Result, StreamResult
from app.executor.executor import Executor
from app.registry.skill_registry import SkillRegistry
from app.scheduler.intent_router import RoutingResult
from app.scheduler.intents import Intent
from app.scheduler.router_scheduler import RouterScheduler
from app.skills.translation.skill import TranslationSkill


def test_execute_ok():
    skill = TranslationSkill()
    with patch("app.skills.translation.service.ollama_client") as mock_llm:
        mock_llm.chat.return_value = "Hello world"
        r = skill.execute(text="你好世界", target_lang="英文")
        assert isinstance(r, Result)
        assert r.as_text() == "Hello world"
        # 检查 prompt 包含目标语言
        args, kwargs = mock_llm.chat.call_args
        user_msg = kwargs["messages"][-1]["content"]
        assert "英文" in user_msg
        assert "你好世界" in user_msg


def test_execute_with_glossary():
    skill = TranslationSkill()
    with patch("app.skills.translation.service.ollama_client") as mock_llm:
        mock_llm.chat.return_value = "ok"
        skill.execute(
            text="Agent框架", target_lang="英文",
            glossary="Agent=智能体\n框架=framework",
        )
        args, kwargs = mock_llm.chat.call_args
        system_msg = kwargs["messages"][0]["content"]
        assert "术语表" in system_msg
        assert "Agent=智能体" in system_msg


def test_stream_yields_chunks():
    skill = TranslationSkill()
    with patch("app.skills.translation.service.ollama_client") as mock_llm:
        mock_llm.stream.return_value = iter(["Hello", " ", "world"])
        chunks = list(skill.stream(text="你好世界", target_lang="英文"))
        assert chunks == ["Hello", " ", "world"]


def test_executor_runs():
    skill = TranslationSkill()
    with patch("app.skills.translation.service.ollama_client") as mock_llm:
        mock_llm.chat.return_value = "Hi"
        r = Executor().run(skill, {"text": "hi", "target_lang": "英文"})
        assert r.as_text() == "Hi"


def test_executor_stream():
    skill = TranslationSkill()
    with patch("app.skills.translation.service.ollama_client") as mock_llm:
        mock_llm.stream.return_value = iter(["a", "b"])
        ctx = RequestContext(text="hi", want_stream=True)
        r = Executor().run(skill, {"text": "hi", "target_lang": "英文"}, ctx=ctx)
        assert isinstance(r, StreamResult)
        assert list(r) == ["a", "b"]


def test_executor_missing_target_lang():
    skill = TranslationSkill()
    r = Executor().run(skill, {"text": "hi"})
    assert "缺少必填参数" in r.as_text()


def test_router_selects_translation():
    reg = SkillRegistry()
    reg.register(TranslationSkill())
    router = MagicMock()
    router.route.return_value = RoutingResult(
        intent=Intent.TRANSLATION, confidence=0.95,
        params={"target_lang": "英文"}, source="llm",
    )
    scheduler = RouterScheduler(router=router, skill_registry=reg)
    ctx = RequestContext(text="翻译成英文 hello", session_id="s1")
    decision = scheduler.route(ctx)
    assert decision.skill.name == "translate_text"
    assert decision.kwargs["target_lang"] == "英文"
    assert decision.kwargs["text"] == "翻译成英文 hello"