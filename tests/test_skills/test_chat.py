"""Chat Skill 测试：直调 + 流式 + 路由 + 反例。"""
from unittest.mock import MagicMock, patch

import pytest

from app.contracts.context import RequestContext
from app.contracts.result import Result
from app.executor.executor import Executor
from app.scheduler.intents import Intent
from app.scheduler.intent_router import RoutingResult
from app.scheduler.router_scheduler import RouterScheduler
from app.registry.skill_registry import SkillRegistry
from app.skills.chat.skill import ChatSkill


# ============ 直调 ============

def test_execute_calls_llm():
    skill = ChatSkill()
    with patch("app.skills.chat.service.ollama_client") as mock_llm:
        mock_llm.chat.return_value = "你好，我是小Agent"
        r = skill.execute(text="你好")
        assert isinstance(r, Result)
        assert r.as_text() == "你好，我是小Agent"
        assert mock_llm.chat.called


def test_execute_with_history():
    skill = ChatSkill()
    with patch("app.skills.chat.service.ollama_client") as mock_llm:
        mock_llm.chat.return_value = "OK"
        history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        skill.execute(text="how are you", history=history)
        args, kwargs = mock_llm.chat.call_args
        messages = kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert messages[1]["content"] == "hi"
        assert messages[2]["content"] == "hello"
        assert messages[-1]["content"] == "how are you"


def test_execute_empty_history():
    skill = ChatSkill()
    with patch("app.skills.chat.service.ollama_client") as mock_llm:
        mock_llm.chat.return_value = "ok"
        skill.execute(text="hi", history=None)
        args, kwargs = mock_llm.chat.call_args
        assert len(kwargs["messages"]) == 2  # system + user


def test_execute_llm_error_returns_message():
    skill = ChatSkill()
    with patch("app.skills.chat.service.ollama_client") as mock_llm:
        mock_llm.chat.side_effect = RuntimeError("connection refused")
        r = skill.execute(text="hi")
        assert "失败" in r.as_text()


# ============ 流式 ============

def test_stream_yields_chunks():
    skill = ChatSkill()
    with patch("app.skills.chat.service.ollama_client") as mock_llm:
        mock_llm.stream.return_value = iter(["你", "好", "！"])
        chunks = list(skill.stream(text="hi"))
        assert chunks == ["你", "好", "！"]


def test_stream_error_yields_message():
    skill = ChatSkill()
    with patch("app.skills.chat.service.ollama_client") as mock_llm:
        def gen():
            yield "a"
            raise RuntimeError("boom")
        mock_llm.stream.return_value = gen()
        chunks = list(skill.stream(text="hi"))
        assert chunks[0] == "a"
        assert "失败" in chunks[-1]


# ============ Executor 接入 ============

def test_executor_runs_chat():
    skill = ChatSkill()
    with patch("app.skills.chat.service.ollama_client") as mock_llm:
        mock_llm.chat.return_value = "hi there"
        r = Executor().run(skill, {"text": "hi"})
        assert r.as_text() == "hi there"


def test_executor_uses_stream_when_requested():
    from app.contracts.result import StreamResult
    skill = ChatSkill()
    with patch("app.skills.chat.service.ollama_client") as mock_llm:
        mock_llm.stream.return_value = iter(["a", "b"])
        ctx = RequestContext(text="hi", want_stream=True)
        r = Executor().run(skill, {"text": "hi"}, ctx=ctx)
        assert isinstance(r, StreamResult)
        assert list(r) == ["a", "b"]


# ============ 路由 ============

def test_router_scheduler_selects_chat():
    reg = SkillRegistry()
    reg.register(ChatSkill())

    router = MagicMock()
    router.route.return_value = RoutingResult(
        intent=Intent.CHAT, confidence=0.95, params={}, source="llm",
    )

    scheduler = RouterScheduler(router=router, skill_registry=reg)
    ctx = RequestContext(text="今天有点累", session_id="s1", user_id=1)
    decision = scheduler.route(ctx)
    assert decision.skill is not None
    assert decision.skill.name == "chat"
    assert decision.kwargs["text"] == "今天有点累"


# ============ 反例 ============

def test_executor_missing_text():
    skill = ChatSkill()
    r = Executor().run(skill, {})
    assert "缺少必填参数" in r.as_text()


def test_executor_wrong_type():
    skill = ChatSkill()
    r = Executor().run(skill, {"text": 123})
    assert "应为 string" in r.as_text()