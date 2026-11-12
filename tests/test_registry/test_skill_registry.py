# tests/test_registry/test_skill_registry.py
from app.contracts.result import Result, text_part
from app.contracts.skill import BaseSkill
from app.registry.skill_registry import SkillRegistry


class EchoSkill(BaseSkill):
    name = "echo"
    description = "echo"
    schema = {"type": "object", "properties": {}, "required": []}
    intent = "echo"
    category = "general"

    def execute(self, **kw):
        return Result(parts=[text_part("hi")])


class OtherSkill(BaseSkill):
    name = "other"
    description = "other"
    schema = {"type": "object", "properties": {}, "required": []}
    intent = None
    category = "utility"

    def execute(self, **kw):
        return Result(parts=[text_part("ok")])


def test_register_and_find():
    reg = SkillRegistry()
    reg.register(EchoSkill())
    assert reg.by_name("echo").name == "echo"
    assert reg.by_intent("echo").name == "echo"
    assert reg.names() == ["echo"]


def test_register_without_intent():
    reg = SkillRegistry()
    reg.register(OtherSkill())
    assert reg.by_intent("other") is None
    assert reg.by_name("other").name == "other"


def test_get_schemas():
    reg = SkillRegistry()
    reg.register(EchoSkill())
    schemas = reg.get_schemas()
    assert len(schemas) == 1
    assert schemas[0]["function"]["name"] == "echo"


def test_stats():
    reg = SkillRegistry()
    reg.register(EchoSkill())
    reg.register(OtherSkill())
    s = reg.stats()
    assert s["total"] == 2
    assert s["with_intent"] == 1