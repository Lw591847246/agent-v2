"""Word Skill 测试。"""
from unittest.mock import MagicMock, patch

from app.contracts.context import RequestContext
from app.contracts.result import Result
from app.executor.executor import Executor
from app.registry.skill_registry import SkillRegistry
from app.scheduler.intent_router import RoutingResult
from app.scheduler.intents import Intent
from app.scheduler.router_scheduler import RouterScheduler
from app.skills.word.skill import WordSkill


def test_execute_submits_async():
    skill = WordSkill()
    with patch("app.skills.word.skill.word_service") as mock_svc:
        mock_svc.submit.return_value = "word_abc"
        r = skill.execute(content="写一份报告", session_id="s1", user_id=1)
        assert r.task_id == "word_abc"
        assert r.parts[0]["card_type"] == "skill_progress"


def test_execute_empty_content():
    skill = WordSkill()
    r = skill.execute(content="")
    assert "缺少" in r.as_text()


def test_run_async_success():
    from app.skills.word.service import WordService
    svc = WordService()
    with patch("app.skills.word.service.model_manager") as mock_llm, \
         patch("app.skills.word.service.word_builder") as mock_builder, \
         patch("app.skills.word.service.task_runner") as mock_runner:
        mock_llm.chat.return_value = "# 报告\n\n内容..."
        mock_builder.build.return_value = "s1/word_1.docx"
        mock_builder.OUTPUT_ROOT = "./data/word_output"

        svc._run_async("tid", "写一份报告", "", "s1", [], [])

        mock_runner.finish.assert_called_once()
        kwargs = mock_runner.finish.call_args[1]
        assert kwargs["outputs"][0]["kind"] == "file"
        assert kwargs["parts"][0]["card_type"] == "file_download"


def test_run_async_history_export():
    """历史故事直接导出，不走 LLM。"""
    from app.skills.word.service import WordService
    svc = WordService()
    history = [
        {"role": "assistant", "content": "# 故事\n" + "从前有座山，" * 50},
    ]
    with patch("app.skills.word.service.word_builder") as mock_builder, \
         patch("app.skills.word.service.model_manager") as mock_llm, \
         patch("app.skills.word.service.task_runner") as mock_runner:
        mock_builder.build.return_value = "s1/word_1.docx"
        mock_builder.OUTPUT_ROOT = "./data/word_output"

        svc._run_async("tid", "把上一轮的故事导出Word", "", "s1", history, [])

        # 不走 LLM
        mock_llm.chat.assert_not_called()
        mock_runner.finish.assert_called_once()


def test_router_selects_word():
    reg = SkillRegistry()
    reg.register(WordSkill())
    router = MagicMock()
    router.route.return_value = RoutingResult(
        intent=Intent.WORD_GENERATION, confidence=0.9, params={}, source="llm",
    )
    scheduler = RouterScheduler(router=router, skill_registry=reg)
    ctx = RequestContext(text="生成Word", session_id="s1", user_id=1)
    decision = scheduler.route(ctx)
    assert decision.skill.name == "generate_word"