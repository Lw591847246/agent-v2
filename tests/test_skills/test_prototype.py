"""Prototype Skill 测试。"""
from unittest.mock import patch

from app.contracts.context import RequestContext
from app.contracts.result import Result
from app.executor.executor import Executor
from app.registry.skill_registry import SkillRegistry
from app.scheduler.intent_router import RoutingResult
from app.scheduler.intents import Intent
from app.scheduler.router_scheduler import RouterScheduler
from app.skills.prototype.skill import PrototypeSkill
from app.skills.prototype.service import _extract_html


def test_extract_html_from_markdown():
    raw = "```html\n<!DOCTYPE html>\n<html><body>hi</body></html>\n```"
    html = _extract_html(raw)
    assert html.startswith("<!DOCTYPE html>")
    assert "</html>" in html


def test_extract_html_with_explanation():
    raw = "好的，以下是原型：\n\n<!DOCTYPE html>\n<html><body>x</body></html>\n\n请查收。"
    html = _extract_html(raw)
    assert html.startswith("<!DOCTYPE html>")
    assert html.endswith("</html>")


def test_execute_ok():
    skill = PrototypeSkill()
    with patch("app.skills.prototype.skill.prototype_service") as mock_svc:
        mock_svc.generate.return_value = {
            "type": "file",
            "title": "登录页",
            "filename": "登录页_123.html",
            "path": "./data/prototype_output/s1/登录页_123.html",
            "url": "/agent/v1/skill/download?path=...",
            "html": "<!DOCTYPE html><html><body>login</body></html>",
        }
        r = skill.execute(requirement="做一个登录页", session_id="s1", user_id=1)
        assert isinstance(r, Result)
        assert r.parts[0]["card_type"] == "file_download"
        assert len(r.outputs) == 1
        assert r.outputs[0]["kind"] == "file"


def test_execute_empty_requirement():
    skill = PrototypeSkill()
    r = skill.execute(requirement="")
    assert "请描述" in r.as_text()


def test_execute_llm_error():
    skill = PrototypeSkill()
    with patch("app.skills.prototype.skill.prototype_service") as mock_svc:
        mock_svc.generate.return_value = {"type": "text", "content": "生成失败"}
        r = skill.execute(requirement="登录页")
        assert "失败" in r.as_text()


def test_executor_missing_requirement():
    skill = PrototypeSkill()
    r = Executor().run(skill, {})
    assert "缺少必填参数" in r.as_text()


def test_router_selects():
    reg = SkillRegistry()
    reg.register(PrototypeSkill())
    from unittest.mock import MagicMock
    router = MagicMock()
    router.route.return_value = RoutingResult(
        intent=Intent.PROTOTYPE, confidence=0.9, params={}, source="llm",
    )
    scheduler = RouterScheduler(router=router, skill_registry=reg)
    ctx = RequestContext(text="做一个登录页原型", session_id="s1", user_id=1)
    decision = scheduler.route(ctx)
    assert decision.skill.name == "generate_prototype"