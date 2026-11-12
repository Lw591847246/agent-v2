"""DocumentSummarize Skill 测试。"""
from unittest.mock import patch

from app.contracts.result import Result
from app.executor.executor import Executor
from app.skills.document_summarize.skill import DocumentSummarizeSkill


def test_execute_single():
    skill = DocumentSummarizeSkill()
    with patch(
        "app.skills.document_summarize.skill.document_summarize_service"
    ) as mock_svc:
        mock_svc.summarize.return_value = "# 摘要\n\n核心内容..."
        r = skill.execute(file_id="doc1", user_query="提取要点")
        assert "# 摘要" in r.as_text()
        mock_svc.summarize.assert_called_once_with("doc1", "提取要点")


def test_execute_batch():
    skill = DocumentSummarizeSkill()
    with patch(
        "app.skills.document_summarize.skill.document_summarize_service"
    ) as mock_svc:
        mock_svc.summarize_batch.return_value = "合并摘要"
        r = skill.execute(file_ids=["d1", "d2"], user_query="汇总")
        assert r.as_text() == "合并摘要"
        mock_svc.summarize_batch.assert_called_once_with(["d1", "d2"], "汇总")


def test_execute_no_file():
    skill = DocumentSummarizeSkill()
    r = skill.execute()
    assert "未提供文档" in r.as_text()