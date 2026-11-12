"""Mindmap Skill 测试。"""
from unittest.mock import MagicMock, patch

from app.contracts.context import RequestContext
from app.contracts.result import Result
from app.executor.executor import Executor
from app.registry.skill_registry import SkillRegistry
from app.scheduler.intent_router import RoutingResult
from app.scheduler.intents import Intent
from app.scheduler.router_scheduler import RouterScheduler
from app.skills.mindmap.skill import MindmapSkill
from app.skills.mindmap.service import _tree_to_markdown


def test_tree_to_markdown():
    tree = {
        "name": "AI 架构",
        "children": [
            {"name": "路由层", "children": [{"name": "L1"}, {"name": "L2"}]},
            {"name": "调度层", "children": []},
        ],
    }
    md = _tree_to_markdown(tree)
    assert "# AI 架构" in md
    assert "路由层" in md
    assert "L1" in md


def test_execute_ok():
    skill = MindmapSkill()
    with patch("app.skills.mindmap.skill.mindmap_service") as mock_svc:
        mock_svc.generate.return_value = {
            "type": "file",
            "title": "AI 架构",
            "filename": "AI_架构_123.md",
            "path": "./data/mindmap_output/s1/AI_架构_123.md",
            "url": "/agent/v1/skill/download?path=...",
            "markdown": "# AI 架构\n\n- 路由层",
        }
        r = skill.execute(topic="AI 架构", session_id="s1", user_id=1)
        assert isinstance(r, Result)
        assert r.parts[0]["type"] == "card"
        assert r.parts[0]["card_type"] == "file_download"
        assert len(r.outputs) == 1
        assert r.outputs[0]["kind"] == "file"


def test_execute_empty_topic():
    skill = MindmapSkill()
    r = skill.execute(topic="")
    assert "请告诉我" in r.as_text()


def test_execute_llm_error():
    skill = MindmapSkill()
    with patch("app.skills.mindmap.skill.mindmap_service") as mock_svc:
        mock_svc.generate.return_value = {"type": "text", "content": "生成失败"}
        r = skill.execute(topic="测试")
        assert "失败" in r.as_text()


def test_executor_missing_topic():
    skill = MindmapSkill()
    r = Executor().run(skill, {})
    assert "缺少必填参数" in r.as_text()


def test_router_selects():
    reg = SkillRegistry()
    reg.register(MindmapSkill())
    router = MagicMock()
    router.route.return_value = RoutingResult(
        intent=Intent.MINDMAP, confidence=0.9, params={}, source="llm",
    )
    scheduler = RouterScheduler(router=router, skill_registry=reg)
    ctx = RequestContext(text="生成AI架构思维导图", session_id="s1", user_id=1)
    decision = scheduler.route(ctx)
    assert decision.skill.name == "generate_mindmap"