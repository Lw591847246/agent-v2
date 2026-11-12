"""ChatService.handle 调用链测试。"""
from unittest.mock import MagicMock, patch

from app.application.chat_service import ChatService
from app.contracts.result import Result, text_part
from app.contracts.scheduler import RoutingDecision
from app.contracts.skill import BaseSkill


class EchoSkill(BaseSkill):
    name = "chat"
    description = "echo"
    schema = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
    intent = "chat"
    category = "general"

    def execute(self, text, **kw):
        return Result(parts=[text_part(f"echo:{text}")])


def test_handle_non_stream():
    scheduler = MagicMock()
    scheduler.route.return_value = RoutingDecision(
        skill=EchoSkill(), kwargs={"text": "hi"}, intent="chat", confidence=0.9,
    )
    svc = ChatService(scheduler=scheduler)

    with patch("app.application.chat_service.conversation_service") as mock_conv, \
         patch("app.application.chat_service.context_provider") as mock_ctx, \
         patch("app.application.chat_service.conversation_orchestrator") as mock_orch:
        mock_conv.exists_user_message_by_client_id.return_value = False
        mock_conv.message_count.return_value = 2
        mock_ctx.build.return_value = MagicMock(session_id="s1", params={})

        hr = svc.handle(text="hi", history=[], user_id=1, session_id="s1")
        assert not hr.is_stream
        assert hr.result.as_text() == "echo:hi"

        msg_id = svc.finalize(hr, parts=hr.result.parts)
        assert mock_conv.save_assistant_message.called
        assert mock_orch.after_turn.called


def test_handle_no_skill_falls_back():
    scheduler = MagicMock()
    scheduler.route.return_value = RoutingDecision(skill=None, kwargs={})
    svc = ChatService(scheduler=scheduler)

    with patch("app.application.chat_service.conversation_service") as mock_conv, \
         patch("app.application.chat_service.context_provider") as mock_ctx:
        mock_conv.exists_user_message_by_client_id.return_value = False
        mock_conv.message_count.return_value = 2
        mock_ctx.build.return_value = MagicMock(session_id="s1", params={})

        hr = svc.handle(text="hi", history=[], user_id=1, session_id="s1")
        assert not hr.is_stream
        assert "无法识别意图" in hr.result.as_text()