"""Knowledge Skill 测试。"""
from unittest.mock import patch

from app.contracts.result import Result
from app.executor.executor import Executor
from app.skills.knowledge.skill import KnowledgeSkill


def test_execute_hit():
    skill = KnowledgeSkill()
    with patch("app.skills.knowledge.skill.knowledge_service") as mock_svc:
        mock_svc.query.return_value = [
            {"content": "Agent 是智能体", "metadata": {"doc_id": "d1", "chunk_index": 0}},
        ]
        mock_svc.ask.return_value = "Agent 是智能体 [引用: 1]"
        r = skill.execute(question="Agent 是什么")
        assert isinstance(r, Result)
        assert "Agent" in r.as_text()


def test_execute_miss_falls_back_to_chat():
    skill = KnowledgeSkill()
    with patch("app.skills.knowledge.skill.knowledge_service") as mock_svc, \
         patch("app.skills.chat.service.chat_service") as mock_chat:
        mock_svc.query.return_value = []
        mock_chat.chat.return_value = "通用回答"
        r = skill.execute(question="随便问问")
        assert r.as_text() == "通用回答"


def test_executor_missing_question():
    skill = KnowledgeSkill()
    r = Executor().run(skill, {})
    assert "缺少必填参数" in r.as_text()