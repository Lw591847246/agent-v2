# app/executor/executor.py
"""唯一执行入口：schema 校验 + 调 Skill + Result 封装。"""
from __future__ import annotations

from typing import Any, Optional

from app.contracts.action import ActionRequest
from app.contracts.context import RequestContext
from app.contracts.errors import SkillError, ValidationError
from app.contracts.result import Result, StreamResult, error_part
from app.contracts.skill import BaseSkill
from app.core.logger import logger

from contextlib import nullcontext as _nullctx


class Executor:

    def run(
        self,
        skill: BaseSkill,
        kwargs: dict,
        ctx: Optional[RequestContext] = None,
    ):
        """
        返回 Result 或 StreamResult。
        - ctx.want_stream=True 且 Skill 有自定义 stream → StreamResult
        - 否则 → Result
        """
        # 1. 参数校验
        try:
            self._validate(kwargs, skill.schema)
        except ValidationError as e:
            logger.warning(f"[Executor] {skill.name} 参数校验失败: {e}")
            return Result(parts=[error_part(str(e))])

        # 2. 执行
        try:
            if ctx is not None and ctx.want_stream and type(skill).stream is not BaseSkill.stream:
                return StreamResult(skill.stream(**kwargs))
            return skill.execute(**kwargs)
        except SkillError as e:
            logger.warning(f"[Executor] {skill.name} 业务异常: {e}")
            return Result(parts=[error_part(e.message)])
        except NotImplementedError as e:
            logger.warning(f"[Executor] {skill.name} 未实现: {e}")
            return Result(parts=[error_part("该能力暂未实现")])
        except Exception as e:
            logger.exception(f"[Executor] {skill.name} 执行崩溃: {e}")
            return Result(parts=[error_part("执行失败")])

    # app/executor/executor.py 中：
    def run_action(self, skill: BaseSkill, action: ActionRequest) -> Result:
        from app.core.gpu_lock import GPUResource, gpu_lock, _map_task_name
        try:
            # skill.name → GPUResource；未知的 skill 不加锁（纯文本类）
            resource = None
            try:
                resource = _map_task_name(skill.name)
            except ValueError:
                resource = None
            ctx = gpu_lock.lease(resource, task=f"action:{skill.name}", timeout=600) \
                if resource else _nullctx()
            with ctx:
                return skill.on_action(action)
        except NotImplementedError:
            return Result(parts=[error_part("该操作不支持")])
        except SkillError as e:
            return Result(parts=[error_part(e.message)])
        except Exception as e:
            logger.exception(f"[Executor] {skill.name} action 崩溃: {e}")
            return Result(parts=[error_part("执行失败")])

    # ---------- schema 轻量校验 ----------
    @staticmethod
    def _validate(kwargs: dict, schema: dict) -> None:
        required = schema.get("required", [])
        for field in required:
            if field not in kwargs or kwargs[field] is None:
                raise ValidationError(f"缺少必填参数: {field}")

        props = schema.get("properties", {})
        for field, spec in props.items():
            if field not in kwargs:
                continue
            v = kwargs[field]
            expected = spec.get("type")
            if not expected or v is None:
                continue
            if expected == "string" and not isinstance(v, str):
                raise ValidationError(f"{field} 应为 string，实际 {type(v).__name__}")
            if expected == "integer" and not isinstance(v, int):
                raise ValidationError(f"{field} 应为 integer，实际 {type(v).__name__}")
            if expected == "number" and not isinstance(v, (int, float)):
                raise ValidationError(f"{field} 应为 number，实际 {type(v).__name__}")
            if expected == "boolean" and not isinstance(v, bool):
                raise ValidationError(f"{field} 应为 boolean，实际 {type(v).__name__}")
            if expected == "array" and not isinstance(v, list):
                raise ValidationError(f"{field} 应为 array，实际 {type(v).__name__}")


executor = Executor()