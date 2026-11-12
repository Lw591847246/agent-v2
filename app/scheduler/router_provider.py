"""Router 参数提供器：把 RequestContext 归一化为 Skill kwargs。"""
from __future__ import annotations

from typing import Any

from app.contracts.context import RequestContext
from app.contracts.errors import ValidationError
from app.contracts.scheduler import BaseParameterProvider
from app.contracts.skill import BaseSkill
from app.core.logger import logger


class RouterParameterProvider(BaseParameterProvider):

    def provide(self, source: Any, skill: BaseSkill, raw: dict) -> dict:
        if not isinstance(source, RequestContext):
            raise ValidationError("RouterParameterProvider 需要 RequestContext")

        ctx: RequestContext = source
        l2_params = raw or {}
        kwargs: dict = {}

        schema_props = skill.schema.get("properties", {})
        for field, spec in schema_props.items():
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
            elif field == "file_id" or field.endswith("_file_id"):
                att_type = self._infer_attachment_type(field, skill)
                att = ctx.first_attachment(att_type) if att_type else None
                if att:
                    kwargs[field] = att.file_id
            elif field == "history":
                kwargs[field] = ctx.history
            elif field == "session_id":
                kwargs[field] = ctx.session_id
            elif field == "user_id":
                kwargs[field] = ctx.user_id
            elif field == "flow_context":
                kwargs[field] = ctx.flow_context

        # ★ 无条件注入系统字段（修复图片找不到 msg_id 的关键）
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
        if skill.requires_attachments:
            return skill.requires_attachments[0]
        return ""


router_parameter_provider = RouterParameterProvider()