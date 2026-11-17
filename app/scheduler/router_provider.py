"""Router 参数提供器：把 RequestContext 归一化为 Skill kwargs。"""
from __future__ import annotations

from typing import Any

from app.contracts.context import RequestContext
from app.contracts.errors import ValidationError
from app.contracts.scheduler import BaseParameterProvider
from app.contracts.skill import BaseSkill
from app.core.logger import logger


# ⭐ file_id 类的无效值（LLM 编造 / 空占位符），出现这些值就改为从 attachments 取
_INVALID_FILE_ID_VALUES = {"", "...", "…", "null", "None", "undefined", "N/A", "n/a", "-"}


class RouterParameterProvider(BaseParameterProvider):

    def provide(self, source: Any, skill: BaseSkill, raw: dict) -> dict:
        if not isinstance(source, RequestContext):
            raise ValidationError("RouterParameterProvider 需要 RequestContext")

        ctx: RequestContext = source
        l2_params = raw or {}
        kwargs: dict = {}

        schema_props = skill.schema.get("properties", {})
        for field, spec in schema_props.items():
            # ⭐ 判断是不是 file_id 类字段
            is_file_field = (field == "file_id") or field.endswith("_file_id")

            if is_file_field:
                # ⭐ 优先从 attachments 注入
                att_type = self._infer_attachment_type(field, skill)
                att = ctx.first_attachment(att_type) if att_type else None

                # ⭐ L2 给的 file_id 如果是有效值就用，否则用 attachments 的
                l2_val = l2_params.get(field)
                if l2_val and str(l2_val).strip() not in _INVALID_FILE_ID_VALUES:
                    kwargs[field] = l2_val
                elif att:
                    kwargs[field] = att.file_id
                    logger.info(
                        f"[RouterProvider] {field} 由 attachments 注入: {att.file_id} "
                        f"(L2 给的={l2_val!r})"
                    )
                continue

            # 其他字段：L2 优先
            if field in l2_params and l2_params[field] is not None:
                kwargs[field] = l2_params[field]
                continue

            if field in (
                "text", "question", "requirement", "prompt",
                "content", "topic", "user_text", "query",
            ):
                kwargs[field] = ctx.text
            elif field == "raw_text":
                kwargs[field] = ctx.text
            elif field == "history":
                kwargs[field] = ctx.history
            elif field == "session_id":
                kwargs[field] = ctx.session_id
            elif field == "user_id":
                kwargs[field] = ctx.user_id
            elif field == "flow_context":
                kwargs[field] = ctx.flow_context

        kwargs.setdefault("session_id", ctx.session_id)
        kwargs.setdefault("user_id", ctx.user_id)
        kwargs["attachments_obj"] = ctx.attachments

        required = skill.schema.get("required", [])
        for f in required:
            if f not in kwargs or kwargs[f] in (None, ""):
                raise ValidationError(f"Skill {skill.name} 缺少必填参数: {f}")

        logger.info(
            f"[RouterProvider] skill={skill.name} "
            f"kwargs_keys={list(kwargs.keys())} "
            f"file_id={kwargs.get('file_id', '(none)')!r} "
            f"session={ctx.session_id[:8] if ctx.session_id else '(EMPTY!)'} "
            f"user={ctx.user_id}"
        )
        return kwargs

    @staticmethod
    def _infer_attachment_type(field: str, skill: BaseSkill) -> str:
        if "image" in field:
            return "image"
        if "video" in field:
            return "video"
        if "doc" in field:
            return "doc"
        if "audio" in field:
            return "audio"
        # ⭐ 如果 Skill 声明了 requires_attachments，用它
        if skill.requires_attachments:
            return skill.requires_attachments[0]
        # ⭐ 如果都不是，但字段叫 file_id 且 Skill 声明了 produces_outputs=False 且有 doc 意图
        # 默认按 doc 处理（document_qa / document_summarize 都是 doc）
        return "doc"


router_parameter_provider = RouterParameterProvider()