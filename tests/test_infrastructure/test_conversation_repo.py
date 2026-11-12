"""ConversationRepo.update_message_content outputs 测试。"""
import json
from unittest.mock import patch

from app.infrastructure.repositories.conversation_repo import ConversationRepoImpl


def test_update_message_content_outputs():
    repo = ConversationRepoImpl()
    with patch.object(repo, "execute") as mock_exec:
        repo.update_message_content(
            message_id=1,
            content='{"a":1}',
            outputs=[{"kind": "video", "filename": "v.mp4", "url": "/v"}],
        )
        sql, params = mock_exec.call_args[0]
        assert "outputs=%s" in sql
        assert any(p and "video" in str(p) for p in params)


def test_update_message_content_without_outputs():
    repo = ConversationRepoImpl()
    with patch.object(repo, "execute") as mock_exec:
        repo.update_message_content(message_id=2, content="hi")
        sql, _ = mock_exec.call_args[0]
        assert "outputs=%s" not in sql