# tests/test_executor/test_executor.py
from app.contracts.result import Result, StreamResult, text_part
from app.contracts.skill import BaseSkill
from app.executor.executor import Executor


class EchoSkill(BaseSkill):
    name = "echo"
    description = "echo"
    schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    intent = "echo"
    category = "general"

    def execute(self, text, **kw):
        return Result(parts=[text_part(text)])


def test_execute_ok():
    e = Executor()
    r = e.run(EchoSkill(), {"text": "hi"})
    assert isinstance(r, Result)
    assert r.as_text() == "hi"


def test_execute_missing_required():
    e = Executor()
    r = e.run(EchoSkill(), {})
    assert "缺少必填参数" in r.as_text()


def test_execute_wrong_type():
    e = Executor()
    r = e.run(EchoSkill(), {"text": 123})
    assert "应为 string" in r.as_text()


def test_execute_stream_when_requested():
    class StreamSkill(EchoSkill):
        def stream(self, text, **kw):
            yield "a"
            yield "b"

    from app.contracts.context import RequestContext
    ctx = RequestContext(text="x", want_stream=True)
    e = Executor()
    r = e.run(StreamSkill(), {"text": "x"}, ctx=ctx)
    assert isinstance(r, StreamResult)
    assert list(r) == ["a", "b"]