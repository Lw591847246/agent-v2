"""PPT 业务：LLM 出 outline + builder 构建 + 异步任务。"""
from __future__ import annotations

import json
import os
import time

from app.config.loader import config
from app.context.task_runner import task_runner
from app.contracts.result import build_output
from app.core.logger import logger
from app.infrastructure.clients.model_manager import model_manager
from app.infrastructure.repositories.conversation_repo import conversation_repo
from app.skills.ppt.builder import dynamic_ppt_builder
from app.skills.ppt.prompts import PROMPT


def _extract_text(obj) -> str:
    if isinstance(obj, str):
        return obj.strip()
    if isinstance(obj, (int, float)):
        return str(obj)
    if isinstance(obj, list):
        return " ".join([_extract_text(x) for x in obj if _extract_text(x)])
    if isinstance(obj, dict):
        texts = []
        for k, v in obj.items():
            if k in ('type', 'key', 'id', 'layout'):
                continue
            t = _extract_text(v)
            if t:
                texts.append(t)
        return "，".join(texts) if len(texts) > 1 else (texts[0] if texts else "")
    return str(obj).strip()


def _parse_json(raw: str) -> dict:
    if not raw or not raw.strip():
        raise ValueError("模型返回了空字符串")
    raw = raw.strip()
    if raw.startswith("```json"):
        raw = raw[7:-3]
    elif raw.startswith("```"):
        raw = raw[3:-3]
    raw = raw.strip()
    l = raw.find("{")
    if l >= 0:
        raw = raw[l:]
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning(f"[Ppt] 标准 JSON 解析失败: {e}")
        try:
            from json_repair import repair_json
            return json.loads(repair_json(raw))
        except Exception as e2:
            logger.error(f"[Ppt] repair_json 也失败: {e2}")
            raise


def _dynamic_compress(full_text: str) -> str:
    max_ctx_tokens = config.get("doc_processing.max_context_tokens", 8192)
    reserved_tokens = config.get("doc_processing.reserved_tokens", 2500)
    chars_per_token = config.get("doc_processing.chars_per_token", 1.5)
    max_allowed_chars = int((max_ctx_tokens - reserved_tokens) * chars_per_token)

    if "【用户上传的文件内容开始】" not in full_text:
        return full_text
    start_idx = full_text.find("【用户上传的文件内容开始】")
    end_idx = full_text.find("【用户上传的文件内容结束】")
    if start_idx == -1 or end_idx == -1:
        return full_text
    doc_content = full_text[start_idx + len("【用户上传的文件内容开始】"):end_idx].strip()
    if len(doc_content) <= max_allowed_chars:
        return full_text
    return (
        f"【用户上传的文件内容开始】\n{doc_content[:max_allowed_chars]}\n"
        f"【用户上传的文件内容结束】\n\n"
        f"{full_text[end_idx + len('【用户上传的文件内容结束】'):].strip()}"
    )


class PptService:

    def submit(
        self, content: str, user_query: str,
        session_id: str, user_id: int,
    ) -> str:
        full_text = content
        if user_query:
            full_text = f"{content}\n\n【用户要求】\n{user_query}"

        task_id = task_runner.submit(
            skill_name="ppt",
            session_id=session_id,
            user_id=user_id,
            slots={"full_text": full_text[:1000]},
            fn=lambda tid: self._run_async(tid, full_text, session_id),
            provider="local",
        )
        return task_id

    def _run_async(self, task_id: str, full_text: str, session_id: str) -> None:
        start = time.time()
        try:
            steps = [
                {"label": "解析要求与上下文", "status": "done"},
                {"label": "智能提炼大纲与文档", "status": "running"},
                {"label": "渲染背景图", "status": "pending"},
                {"label": "组装 PPT 并生成文件", "status": "pending"},
            ]
            task_runner.progress(task_id, 20, "生成大纲中...", steps=steps)

            final_prompt = _dynamic_compress(full_text)
            logger.info(f"[Ppt] 调用 LLM，输入长度={len(final_prompt)}")

            raw = model_manager.chat(
                task_type="chat",
                messages=[
                    {"role": "system", "content": PROMPT},
                    {"role": "user", "content": final_prompt},
                ],
            )
            logger.info(f"[Ppt] LLM 原始输出前 500 字: {raw[:500]!r}")
            logger.info(f"[Ppt] LLM 原始输出总长度: {len(raw)}")

            if not raw or not raw.strip():
                raise RuntimeError("LLM 返回空字符串")

            try:
                outline = _parse_json(raw)
            except Exception as e:
                logger.error(f"[Ppt] JSON 解析失败: {e}")
                raise RuntimeError(f"LLM 返回的 JSON 无法解析: {e}")

            logger.info(f"[Ppt] 解析后 outline keys={list(outline.keys())}")

            raw_slides = outline.get("slides", [])
            logger.info(f"[Ppt] 原始 slides 数量: {len(raw_slides)}")

            if not raw_slides:
                raise RuntimeError(f"LLM 返回的 slides 为空，outline keys={list(outline.keys())}")

            # 清理空白页
            valid_slides = []
            skipped = 0
            for idx, s in enumerate(raw_slides):
                s_type = s.get("type", "content")
                if s_type in ("cover", "end"):
                    valid_slides.append(s)
                    logger.info(f"[Ppt] slide[{idx}] type={s_type} 保留（封面/结尾）")
                    continue

                raw_bullets = s.get("bullets") or s.get("cards") or []
                clean_bullets = []
                if isinstance(raw_bullets, list):
                    for b in raw_bullets:
                        txt = _extract_text(b)
                        if txt and len(txt) > 1:
                            clean_bullets.append(txt)
                elif isinstance(raw_bullets, dict):
                    txt = _extract_text(raw_bullets)
                    if txt:
                        clean_bullets.append(txt)

                if not clean_bullets:
                    skipped += 1
                    logger.warning(
                        f"[Ppt] slide[{idx}] title={s.get('title')!r} 无有效 bullets，跳过。"
                        f"raw_bullets={raw_bullets!r}"
                    )
                    continue

                s["bullets"] = clean_bullets
                valid_slides.append(s)

            logger.info(
                f"[Ppt] 过滤后 valid_slides={len(valid_slides)} "
                f"(原始={len(raw_slides)}, 跳过={skipped})"
            )

            if not valid_slides:
                raise RuntimeError(
                    f"所有 {len(raw_slides)} 个 slide 都被判为空，无法生成 PPT。"
                    f"请检查 LLM 输出的 bullets 字段。"
                )

            outline["slides"] = valid_slides

            steps[1]["status"] = "done"
            steps[2]["status"] = "running"
            task_runner.progress(task_id, 70, "渲染并组装 PPT...", steps=steps)

            logger.info(f"[Ppt] 开始 builder.build(), session_id={session_id}")
            rel_path = dynamic_ppt_builder.build(
                session_id, outline, lambda l, s: None,
            )
            logger.info(f"[Ppt] builder 返回 rel_path={rel_path}")

            duration_ms = int((time.time() - start) * 1000)

            steps[2]["status"] = "done"
            steps[3]["status"] = "done"

            title = outline.get("title", "演示文稿")
            filename = f"{title}.pptx"
            download_url = f"/agent/v1/ppt/download/{rel_path}"
            abs_path = os.path.join(
                dynamic_ppt_builder.OUTPUT_ROOT, rel_path,
            )
            file_size = os.path.getsize(abs_path) if os.path.isfile(abs_path) else 0
            logger.info(f"[Ppt] 生成文件: {abs_path}, 大小={file_size} bytes")

            if file_size < 10000:
                logger.warning(
                    f"[Ppt] ⚠️ 文件过小({file_size}B)，可能 slide 内容为空，"
                    f"valid_slides={len(valid_slides)}"
                )

            result_data = {
                "card_type": "file_download",
                "title": "PPT 生成成功",
                "filename": filename,
                "desc": f"{len(valid_slides)} 页 · {file_size // 1024}KB",
                "download_url": download_url,
                "duration_ms": duration_ms,
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

            msg_id = conversation_repo.get_message_id_by_task_id(session_id, task_id)
            if msg_id:
                task_runner.finish(
                    task_id=task_id,
                    parts=parts,
                    outputs=outputs,
                    output_path=abs_path,
                    duration_ms=duration_ms,
                    session_id=session_id,
                )
                logger.info(f"[Ppt] 完成 task_id={task_id} msg_id={msg_id}")
            else:
                task_runner.finish(
                    task_id=task_id, parts=parts, outputs=outputs,
                    duration_ms=duration_ms, session_id=session_id,
                )

        except Exception as e:
            logger.error(f"[Ppt] 失败 task_id={task_id}: {e}", exc_info=True)
            task_runner.fail(task_id, str(e), session_id=session_id)


ppt_service = PptService()