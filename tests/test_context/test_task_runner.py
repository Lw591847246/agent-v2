"""TaskRunner 测试：用 importlib 拿模块对象，避开实例覆盖模块名的问题。"""
import importlib
from unittest.mock import patch

from app.context.task_runner import TaskRunner


def _module():
    """拿到 app.context.task_runner 模块对象（不是实例）。"""
    return importlib.import_module("app.context.task_runner")


def test_finish_writes_task_and_message():
    runner = TaskRunner()
    mod = _module()
    with patch.object(mod, "task_repo") as mock_task, \
         patch.object(mod, "conversation_repo") as mock_conv:
        mock_task.get.return_value.session_id = "s1"
        mock_conv.get_message_id_by_task_id.return_value = 10

        runner.finish(
            task_id="t1",
            parts=[{"type": "image", "url": "/img"}],
            outputs=[{"kind": "image", "filename": "a.png", "url": "/img"}],
            duration_ms=100,
            session_id="s1",
        )

        mock_task.finish.assert_called_once()
        call = mock_conv.update_message_content.call_args[1]
        assert call["outputs"][0]["kind"] == "image"
        assert call["message_type"] == "image"


def test_fail_writes_error_message():
    runner = TaskRunner()
    mod = _module()
    with patch.object(mod, "task_repo") as mock_task, \
         patch.object(mod, "conversation_repo") as mock_conv:
        mock_task.get.return_value.session_id = "s1"
        mock_conv.get_message_id_by_task_id.return_value = 10

        runner.fail("t1", "boom", session_id="s1")

        mock_task.fail.assert_called_once()
        assert mock_conv.update_message_content.called