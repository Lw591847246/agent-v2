"""DocumentQA Skill 测试。"""
from unittest.mock import MagicMock, patch

from app.contracts.context import RequestContext
from app.contracts.result import Result
from app.executor.executor import Executor
from app.models.attachment import Attachment
from app.registry.skill_registry import SkillRegistry
from app.scheduler.intent_router import RoutingResult
from app.scheduler.intents import Intent
from app.scheduler.router_scheduler import RouterScheduler
from app.skills.document_qa.skill import DocumentQaSkill


def test_execute_ok():
    skill = DocumentQaSkill()
    with patch("app.skills.document_qa.skill.document_qa_service") as mock_svc:
        mock_svc.answer.return_value = "第三章讲了 Agent 架构"
        r = skill.execute(file_id="doc1", question="第三章讲了什么")
        assert r.as_text() == "第三章讲了 Agent 架构"
        mock_svc.answer.assert_called_once_with("doc1", "第三章讲了什么")


def test_execute_doc_not_found():
    skill = DocumentQaSkill()
    with patch(
        "app.skills.document_qa.service.DocumentQaService._read_doc",
        return_value="",
    ):
        r = skill.execute(file_id="missing", question="问题")
        assert "未能解析" in r.as_text()


def test_executor_missing_file_id():
    skill = DocumentQaSkill()
    r = Executor().run(skill, {"question": "hi"})
    assert "缺少必填参数" in r.as_text()


def test_router_selects_with_doc_attachment():
    reg = SkillRegistry()
    reg.register(DocumentQaSkill())
    router = MagicMock()
    router.route.return_value = RoutingResult(
        intent=Intent.DOCUMENT_QA, confidence=0.9, params={}, source="llm",
    )
    scheduler = RouterScheduler(router=router, skill_registry=reg)
    ctx = RequestContext(
        text="第三章讲了什么", session_id="s1",
        attachments=[Attachment(file_id="doc1", type="doc", name="a.pdf")],
    )
    decision = scheduler.route(ctx)
    assert decision.skill.name == "document_qa"
    assert decision.kwargs["file_id"] == "doc1"
    assert decision.kwargs["question"] == "第三章讲了什么"