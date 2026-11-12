"""Code Skill 测试。"""
from unittest.mock import MagicMock, patch

from app.contracts.context import RequestContext
from app.contracts.result import Result, StreamResult
from app.executor.executor import Executor
from app.registry.skill_registry import SkillRegistry
from app.scheduler.intent_router import RoutingResult
from app.scheduler.intents import Intent
from app.scheduler.router_scheduler import RouterScheduler
from app.skills.code.skill import CodeSkill


def test_execute_ok():
    skill = CodeSkill()
    with patch("app.skills.code.service.ollama_client") as mock_llm:
        mock_llm.chat.return_value = "```python\ndef f(): pass\n```"
        r = skill.execute(requirement="写一个函数", language="Python")
        assert "def f" in r.as_text()
        args, kwargs = mock_llm.chat.call_args
        assert "Python" in kwargs["messages"][-1]["content"]


def test_stream_yields_chunks():
    skill = CodeSkill()
    with patch("app.skills.code.service.ollama_client") as mock_llm:
        mock_llm.stream.return_value = iter(["```py", "thon\n", "pass"])
        chunks = list(skill.stream(requirement="写代码"))
        assert chunks == ["```py", "thon\n", "pass"]


def test_executor_missing_requirement():
    skill = CodeSkill()
    r = Executor().run(skill, {})
    assert "缺少必填参数" in r.as_text()


def test_executor_stream_when_requested():
    skill = CodeSkill()
    with patch("app.skills.code.service.ollama_client") as mock_llm:
        mock_llm.stream.return_value = iter(["a", "b"])
        ctx = RequestContext(text="写代码", want_stream=True)
        r = Executor().run(skill, {"requirement": "写代码"}, ctx=ctx)
        assert isinstance(r, StreamResult)
        assert list(r) == ["a", "b"]


def test_router_selects_code():
    reg = SkillRegistry()
    reg.register(CodeSkill())
    router = MagicMock()
    router.route.return_value = RoutingResult(
        intent=Intent.CODE_GENERATION, confidence=0.9,
        params={"language": "Python"}, source="llm",
    )
    scheduler = RouterScheduler(router=router, skill_registry=reg)
    ctx = RequestContext(text="写一个 Python 函数", session_id="s1")
    decision = scheduler.route(ctx)
    assert decision.skill.name == "code_generate"
    assert decision.kwargs["language"] == "Python"
    assert decision.kwargs["requirement"] == "写一个 Python 函数"