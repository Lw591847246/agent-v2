"""FaceSwap Skill 测试。"""
from unittest.mock import MagicMock, patch

from app.contracts.action import ActionRequest
from app.contracts.result import Result
from app.executor.executor import Executor
from app.models.attachment import Attachment
from app.skills.face_swap.skill import FaceSwapSkill


def test_execute_no_active_starts_flow():
    skill = FaceSwapSkill()
    with patch("app.skills.face_swap.skill.flow_state_store") as mock_fs, \
         patch("app.skills.face_swap.skill.fs_flow") as mock_flow:
        mock_fs.get_active.return_value = None
        mock_flow.FLOW_NAME = "face_swap"
        mock_flow.start.return_value = {
            "type": "card",
            "card_json": '{"card_id": "c1", "card_type": "face_swap_asset_input", "title": "补充素材"}',
        }

        r = skill.execute(prompt="换脸", session_id="s1", user_id=1)
        assert isinstance(r, Result)
        assert r.parts[0]["type"] == "card"
        assert r.parts[0]["card_type"] == "face_swap_asset_input"
        mock_flow.start.assert_called_once()


def test_execute_has_active_continues():
    skill = FaceSwapSkill()
    with patch("app.skills.face_swap.skill.flow_state_store") as mock_fs, \
         patch("app.skills.face_swap.skill.fs_flow") as mock_flow:
        mock_fs.get_active.return_value = MagicMock()
        mock_flow.FLOW_NAME = "face_swap"
        mock_flow.continue_from_chat.return_value = {
            "type": "card",
            "card_json": '{"card_id": "c2", "card_type": "face_swap_confirm", "title": "确认"}',
        }

        r = skill.execute(prompt="补充台词", session_id="s1", user_id=1)
        assert r.parts[0]["card_type"] == "face_swap_confirm"
        mock_flow.continue_from_chat.assert_called_once()


def test_on_action_state_not_found():
    skill = FaceSwapSkill()
    with patch("app.skills.face_swap.skill.flow_state_store") as mock_fs:
        mock_fs.load.return_value = None
        r = skill.on_action(ActionRequest(session_id="s1", card_id="c1", option_key="confirm"))
        assert "过期" in r.as_text()


def test_on_action_cancel():
    skill = FaceSwapSkill()
    with patch("app.skills.face_swap.skill.flow_state_store") as mock_fs, \
         patch("app.skills.face_swap.skill.fs_flow") as mock_flow:
        mock_fs.load.return_value = MagicMock(flow_name="face_swap", current_step="confirming")
        mock_flow.on_cancel.return_value = {"type": "text", "content": "已取消"}

        r = skill.on_action(ActionRequest(session_id="s1", card_id="c1", option_key="cancel"))
        assert r.as_text() == "已取消"
        mock_flow.on_cancel.assert_called_once()


def test_on_action_confirm():
    skill = FaceSwapSkill()
    with patch("app.skills.face_swap.skill.flow_state_store") as mock_fs, \
         patch("app.skills.face_swap.skill.fs_flow") as mock_flow:
        mock_fs.load.return_value = MagicMock(flow_name="face_swap", current_step="confirming")
        mock_flow.on_confirm.return_value = {
            "type": "card",
            "card_json": '{"card_id": "c2", "card_type": "face_swap_task", "title": "已提交", "tasks": []}',
        }

        r = skill.on_action(ActionRequest(session_id="s1", card_id="c1", option_key="confirm"))
        assert r.parts[0]["card_type"] == "face_swap_task"
        assert r.parts[0]["status"] == "processing"


def test_on_action_unknown_option():
    skill = FaceSwapSkill()
    with patch("app.skills.face_swap.skill.flow_state_store") as mock_fs:
        mock_fs.load.return_value = MagicMock(flow_name="face_swap", current_step="confirming")
        r = skill.on_action(ActionRequest(session_id="s1", card_id="c1", option_key="unknown_xyz"))
        assert "无法识别" in r.as_text()


def test_on_action_exception_returns_error():
    skill = FaceSwapSkill()
    with patch("app.skills.face_swap.skill.flow_state_store") as mock_fs, \
         patch("app.skills.face_swap.skill.fs_flow") as mock_flow:
        mock_fs.load.return_value = MagicMock(flow_name="face_swap", current_step="confirming")
        mock_flow.on_confirm.side_effect = RuntimeError("boom")
        r = skill.on_action(ActionRequest(session_id="s1", card_id="c1", option_key="confirm"))
        assert "操作失败" in r.as_text()