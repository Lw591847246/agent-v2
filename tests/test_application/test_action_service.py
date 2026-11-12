"""ActionService.handle 调用链测试。"""
from unittest.mock import MagicMock, patch

from app.application.action_service import ActionService
from app.contracts.action import ActionRequest
from app.contracts.result import Result, text_part
from app.contracts.skill import BaseSkill


class ActionSkill(BaseSkill):
    name = "face_swap"
    description = "face_swap"
    schema = {"type": "object", "properties": {}, "required": []}
    intent = "face_swap"
    category = "video"

    def execute(self, **kw):
        return Result(parts=[text_part("start")])

    def on_action(self, action):
        return Result(parts=[text_part(f"action:{action.option_key}")])


def test_handle_state_not_found():
    svc = ActionService()
    with patch("app.application.action_service.flow_state_store") as mock_fs:
        mock_fs.load.return_value = None
        r = svc.handle(ActionRequest(session_id="s1", card_id="c1", option_key="confirm"))
        assert "过期" in r.as_text()


def test_handle_skill_not_found():
    svc = ActionService()
    with patch("app.application.action_service.flow_state_store") as mock_fs, \
         patch("app.application.action_service.registry") as mock_reg:
        mock_fs.load.return_value = MagicMock(skill_name="unknown", intent="unknown")
        mock_reg.by_name.return_value = None
        mock_reg.by_intent.return_value = None
        r = svc.handle(ActionRequest(session_id="s1", card_id="c1"))
        assert "不存在" in r.as_text()


def test_handle_ok():
    svc = ActionService()
    with patch("app.application.action_service.flow_state_store") as mock_fs, \
         patch("app.application.action_service.registry") as mock_reg:
        mock_fs.load.return_value = MagicMock(skill_name="face_swap", intent="face_swap")
        mock_reg.by_name.return_value = ActionSkill()
        r = svc.handle(ActionRequest(session_id="s1", card_id="c1", option_key="confirm"))
        assert r.as_text() == "action:confirm"