"""TaskRepo finish outputs 测试（mock DB）。"""
import json
from unittest.mock import MagicMock, patch

from app.infrastructure.repositories.task_repo import TaskRepo


def test_finish_writes_outputs():
    repo = TaskRepo()
    with patch.object(repo, "update") as mock_update:
        repo.finish(
            task_id="t1",
            parts=[{"type": "image", "url": "/img"}],
            duration_ms=100,
            outputs=[{"kind": "image", "filename": "a.png", "url": "/img"}],
        )
        call = mock_update.call_args
        fields = call[1]
        assert fields["status"] == "done"
        assert fields["progress"] == 100
        assert "outputs_json" in fields
        parsed = json.loads(fields["outputs_json"])
        assert parsed[0]["kind"] == "image"


def test_finish_without_outputs():
    repo = TaskRepo()
    with patch.object(repo, "update") as mock_update:
        repo.finish(task_id="t2", parts=[{"type": "text", "content": "hi"}])
        fields = mock_update.call_args[1]
        assert "outputs_json" not in fields