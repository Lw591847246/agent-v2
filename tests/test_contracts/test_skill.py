# tests/test_contracts/test_skill.py
from app.contracts.result import Result, text_part
from app.contracts.skill import BaseSkill


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


def test_skill_execute():
    s = EchoSkill()
    r = s.execute(text="hi")
    assert r.as_text() == "hi"


def test_skill_stream_default():
    s = EchoSkill()
    chunks = list(s.stream(text="hi"))
    assert chunks == ["hi"]


def test_to_fc_tool():
    s = EchoSkill()
    t = s.to_fc_tool()
    assert t["function"]["name"] == "echo"
    assert t["function"]["parameters"]["required"] == ["text"]


def test_on_action_not_implemented():
    import pytest
    from app.contracts.action import ActionRequest
    s = EchoSkill()
    with pytest.raises(NotImplementedError):
        s.on_action(ActionRequest(session_id="s", card_id="c"))