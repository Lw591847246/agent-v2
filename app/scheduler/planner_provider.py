# app/scheduler/planner_provider.py
"""Planner 参数提供器：解析 $text / $attachments[0].file_id / $step_N.output 占位符。"""
from __future__ import annotations

import copy
from typing import Any

from app.contracts.context import RequestContext
from app.contracts.errors import ValidationError
from app.contracts.scheduler import BaseParameterProvider
from app.contracts.skill import BaseSkill
from app.core.logger import logger


class PlannerParameterProvider(BaseParameterProvider):

    def provide(self, source: Any, skill: BaseSkill, raw: dict) -> dict:
        """
        source: {"ctx": RequestContext, "step_outputs": [...]}
        raw:    plan 步骤的 args（含占位符）
        """
        if isinstance(source, dict):
            ctx: RequestContext = source.get("ctx")
            step_outputs = source.get("step_outputs", [])
        else:
            ctx, step_outputs = source, []

        if not isinstance(ctx, RequestContext):
            raise ValidationError("PlannerParameterProvider 需要 {ctx, step_outputs}")

        resolved = self._resolve_placeholders(copy.deepcopy(raw or {}), ctx, step_outputs)

        # 补齐 session_id/user_id
        resolved.setdefault("session_id", ctx.session_id)
        resolved.setdefault("user_id", ctx.user_id)

        required = skill.schema.get("required", [])
        for f in required:
            if f not in resolved or resolved[f] in (None, ""):
                raise ValidationError(f"Planner 缺少必填参数: {f}")

        return resolved

    @staticmethod
    def _resolve_placeholders(args: dict, ctx: RequestContext, step_outputs: list) -> dict:
        for k, v in list(args.items()):
            if not isinstance(v, str):
                continue
            if v == "$text":
                args[k] = ctx.text
            elif v == "$attachments_text":
                docs = [
                    {"file_id": a.file_id, "name": a.name, "type": a.type}
                    for a in ctx.attachments if a.type != "image"
                ]
                args[k] = docs
            elif v.startswith("$attachments["):
                # $attachments[0].file_id
                try:
                    idx_str, field = v[len("$attachments["):].split("].")
                    idx = int(idx_str)
                    if 0 <= idx < len(ctx.attachments):
                        args[k] = getattr(ctx.attachments[idx], field, "")
                except Exception:
                    args[k] = ""
            elif v.startswith("$step_"):
                # $step_1.output / $step_1.output.file_id
                try:
                    rest = v[len("$step_"):]
                    step_idx = int(rest.split(".")[0]) - 1
                    field = rest.split(".", 2)[2] if rest.count(".") >= 2 else ""
                    if 0 <= step_idx < len(step_outputs):
                        out = step_outputs[step_idx]
                        if field and isinstance(out, dict):
                            args[k] = out.get(field, "")
                        elif isinstance(out, dict):
                            args[k] = out.get("content") or out.get("file_id") or str(out)
                        else:
                            args[k] = str(out)
                except Exception as e:
                    logger.warning(f"[PlannerProvider] 解析 {v} 失败: {e}")
                    args[k] = ""
        return args


planner_parameter_provider = PlannerParameterProvider()