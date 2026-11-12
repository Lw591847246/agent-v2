# app/skills/word/service.py
"""Word 业务：LLM 出 Markdown + builder 构建 + 异步任务。"""
from __future__ import annotations

import json
import os
import time
from typing import List, Optional

from app.context.task_runner import task_runner
from app.contracts.result import build_output
from app.core.logger import logger
from app.infrastructure.clients.model_manager import model_manager
from app.infrastructure.repositories.conversation_repo import conversation_repo
from app.skills.word.builder import word_builder
from app.skills.word.prompts import PROMPT
from app.utils.file_parser import extract_text_from_file


_REFERENT_KEYWORDS = ("上一轮", "上一个", "刚才", "刚刚", "之前", "前面", "上面的", "上述", "这段", "这个", "它")
_EXPORT_KEYWORDS = ("导出", "保存", "转成", "转为", "变成", "做成", "写成", "生成", "存为", "存成")
_REWRITE_KEYWORDS = ("改写", "改一下", "调整", "润色", "重写", "优化", "修改", "改编", "扩写", "缩写", "翻译")


def _extract_text_from_message(msg: dict) -> str:
    content = (msg.get("content") or "").strip()
    if not content:
        return ""
    try:
        parsed = json.loads(content)
        if isinstance(parsed, list):
            texts = []
            for part in parsed:
                if isinstance(part, dict) and part.get("type") == "text":
                    t = part.get("content", "")
                    if t:
                        texts.append(t)
            content = "\n\n".join(texts).strip()
    except (json.JSONDecodeError, TypeError):
        pass
    if content.startswith('{') or content.startswith('['):
        return ""
    return content


def _extract_history_story(history: list, min_len: int = 200) -> Optional[str]:
    for msg in reversed(history or []):
        if msg.get("role") != "assistant":
            continue
        text = _extract_text_from_message(msg)
        if text and len(text) >= min_len:
            return text
    return None


def _extract_title_from_markdown(text: str, default: str = "生成的文档"):
    lines = text.split("\n")
    if lines and lines[0].strip().startswith("# "):
        title = lines[0].strip()[2:].strip()
        body = "\n".join(lines[1:]).strip()
        return (title or default), body
    first_line = lines[0].strip() if lines else ""
    title = first_line[:20] if first_line else default
    return title, text


class WordService:

    def submit(
        self, content: str, user_query: str,
        session_id: str, user_id: int,
        history: List[dict] = None,
        attachments: List[dict] = None,
    ) -> str:
        task_id = task_runner.submit(
            skill_name="word",
            session_id=session_id,
            user_id=user_id,
            slots={"content": content[:500], "user_query": user_query[:200]},
            fn=lambda tid: self._run_async(
                tid, content, user_query, session_id,
                history or [], attachments or [],
            ),
            provider="local",
        )
        return task_id

    def _run_async(
        self, task_id: str, user_text: str, user_query: str, session_id: str,
        history: List[dict], attachments: List[dict],
    ) -> None:
        start = time.time()
        steps = [
            {"label": "解析要求与上下文", "status": "done"},
            {"label": "准备正文内容", "status": "running"},
            {"label": "排版并生成 Word 文件", "status": "pending"},
        ]
        try:
            is_referent = any(k in user_text for k in _REFERENT_KEYWORDS)
            has_export = any(k in user_text for k in _EXPORT_KEYWORDS)
            has_rewrite = any(k in user_text for k in _REWRITE_KEYWORDS)

            hist_story = ""
            if is_referent or len(user_text) < 100:
                hist_story = _extract_history_story(history) or ""

            # 直接导出历史故事
            if hist_story and has_export and not has_rewrite:
                title, body = _extract_title_from_markdown(hist_story, "生成的文档")
                safe_title = title.replace("/", "_").replace("\\", "_")
                rel_path = word_builder.build(session_id, safe_title, body)
                self._finish(task_id, session_id, safe_title, rel_path, start, steps)
                return

            # 构造 full_content
            if hist_story:
                full_content = f"【上一轮的故事】\n{hist_story}\n\n【用户要求】\n{user_text}"
            elif attachments:
                full_content = user_text
                for a in attachments:
                    if a.get("type") == "doc":
                        try:
                            with open(a["path"], "rb") as f:
                                text = extract_text_from_file(f.read(), a["name"])
                            if text:
                                full_content = f"【附件内容】\n{text}\n\n【用户要求】\n{user_text}"
                                break
                        except Exception as e:
                            logger.error(f"[Word任务] 附件解析失败: {e}")
            else:
                full_content = user_text

            steps[1]["status"] = "done"
            steps[2]["status"] = "running"
            task_runner.progress(task_id, 70, "排版生成文档...", steps=steps)

            raw = model_manager.chat(
                task_type="chat",
                messages=[
                    {"role": "system", "content": PROMPT},
                    {"role": "user", "content": full_content},
                ],
            )
            raw = (raw or "").strip()
            if raw.startswith("```markdown"):
                raw = raw[11:-3]
            elif raw.startswith("```"):
                raw = raw[3:-3]
            doc_content = raw.strip()

            title, body = _extract_title_from_markdown(doc_content, "生成的文档")
            safe_title = title.replace("/", "_").replace("\\", "_")
            rel_path = word_builder.build(session_id, safe_title, body)
            self._finish(task_id, session_id, safe_title, rel_path, start, steps)

        except Exception as e:
            logger.error(f"[Word任务] 失败 task_id={task_id}: {e}", exc_info=True)
            task_runner.fail(task_id, str(e), session_id=session_id)

    def _finish(
        self, task_id: str, session_id: str, safe_title: str,
        rel_path: str, start: float, steps: list,
    ) -> None:
        duration_ms = int((time.time() - start) * 1000)
        steps[2]["status"] = "done"

        filename = f"{safe_title}.docx"
        download_url = f"/agent/v1/word/download/{rel_path}"
        abs_path = os.path.join(word_builder.OUTPUT_ROOT, rel_path)

        result_data = {
            "card_type": "file_download",
            "title": "Word 生成成功",
            "filename": filename,
            "desc": "Markdown 转 Word 文档",
            "download_url": download_url,
        }
        parts = [{
            "type": "card",
            "card_type": "file_download",
            "status": "done",
            "data": result_data,
        }]
        outputs = [build_output(
            kind="file",
            filename=filename,
            path=abs_path,
            url=download_url,
            task_id=task_id,
        )]

        task_runner.finish(
            task_id=task_id,
            parts=parts,
            outputs=outputs,
            output_path=abs_path,
            duration_ms=duration_ms,
            session_id=session_id,
        )
        logger.info(f"[WordService] 完成 task_id={task_id}")


word_service = WordService()