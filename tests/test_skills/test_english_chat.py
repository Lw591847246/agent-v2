"""EnglishChat Skill 测试。"""
from unittest.mock import patch

from app.contracts.result import StreamResult
from app.contracts.context import RequestContext
from app.executor.executor import Executor
from app.skills.english_chat.service import EnglishChatService
from app.skills.english_chat.skill import EnglishChatSkill


def test_execute_ok():
    skill = EnglishChatSkill()
    with patch("app.skills.english_chat.service.ollama_client") as mock_llm:
        mock_llm.chat.return_value = "Hey! How's your day?"
        r = skill.execute(text="hi")
        assert "Hey" in r.as_text()


def test_stream_yields():
    skill = EnglishChatSkill()
    with patch("app.skills.english_chat.service.ollama_client") as mock_llm:
        mock_llm.stream.return_value = iter(["Hey", "!", " What's up?"])
        chunks = list(skill.stream(text="hi"))
        assert "".join(chunks) == "Hey! What's up?"


def test_filters_chinese_history():
    """历史里的中文 user 消息应被过滤。"""
    svc = EnglishChatService()
    history = [
        {"role": "user", "content": "我想练习英语"},      # 中文，过滤
        {"role": "user", "content": "hello there"},        # 英文，保留
        {"role": "assistant", "content": "Hi!"},           # 非 user，过滤
    ]
    messages = svc._build_messages("how are you", history)
    # system + 1 英文历史 + user + system reminder
    assert messages[0]["role"] == "system"
    assert messages[1]["content"] == "hello there"
    assert messages[-2]["content"] == "how are you"
    assert messages[-1]["role"] == "system"


def test_looks_english():
    assert EnglishChatService._looks_english("hello world") is True
    assert EnglishChatService._looks_english("你好世界") is False
    assert EnglishChatService._looks_english("hello 世界") is True


def test_executor_stream():
    skill = EnglishChatSkill()
    with patch("app.skills.english_chat.service.ollama_client") as mock_llm:
        mock_llm.stream.return_value = iter(["Hi", "!"])
        ctx = RequestContext(text="hi", want_stream=True)
        r = Executor().run(skill, {"text": "hi"}, ctx=ctx)
        assert isinstance(r, StreamResult)
        assert list(r) == ["Hi", "!"]