"""TTS Skill 测试。"""
from unittest.mock import MagicMock, patch

from app.contracts.result import Result
from app.executor.executor import Executor
from app.skills.tts.skill import TtsSkill


def test_execute_short_sync():
    skill = TtsSkill()
    with patch("app.skills.tts.skill.tts_wrapper") as mock_w:
        mock_w.should_async.return_value = False
        mock_w.synthesize_sync.return_value = {
            "path": "/data/tts/a.wav",
            "filename": "a.wav",
            "url": "/agent/v1/tts/a.wav",
        }
        r = skill.execute(text="你好", voice="zf_xiaoxiao")
        assert r.task_id is None
        assert len(r.parts) == 1
        assert r.parts[0]["type"] == "audio"
        assert r.parts[0]["url"] == "/agent/v1/tts/a.wav"
        assert len(r.outputs) == 1
        assert r.outputs[0]["kind"] == "audio"
        assert r.outputs[0]["filename"] == "a.wav"


def test_execute_long_async():
    skill = TtsSkill()
    with patch("app.skills.tts.skill.tts_wrapper") as mock_w, \
         patch("app.skills.tts.skill.task_runner") as mock_runner:
        mock_w.should_async.return_value = True
        mock_runner.submit.return_value = "tts_task_123"
        r = skill.execute(
            text="a" * 200, voice="zf_xiaoxiao",
            session_id="s1", user_id=1,
        )
        assert r.task_id == "tts_task_123"
        assert r.parts[0]["type"] == "card"
        assert r.parts[0]["card_type"] == "skill_progress"
        assert r.parts[0]["status"] == "processing"
        mock_runner.submit.assert_called_once()


def test_execute_empty_text():
    skill = TtsSkill()
    r = skill.execute(text="")
    assert "请输入" in r.as_text()


def test_execute_sync_error_returns_message():
    skill = TtsSkill()
    with patch("app.skills.tts.skill.tts_wrapper") as mock_w:
        mock_w.should_async.return_value = False
        mock_w.synthesize_sync.side_effect = RuntimeError("TTS down")
        r = skill.execute(text="hi")
        assert "失败" in r.as_text()


def test_executor_missing_text():
    skill = TtsSkill()
    r = Executor().run(skill, {})
    assert "缺少必填参数" in r.as_text()