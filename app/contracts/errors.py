# app/contracts/errors.py
"""契约层异常定义：Skill 内部抛出，Executor 捕获后转 error_part。"""
from __future__ import annotations


class SkillError(Exception):
    """Skill 业务异常基类。"""

    def __init__(self, message: str, code: int = 400):
        self.message = message
        self.code = code
        super().__init__(message)


class ValidationError(SkillError):
    """参数校验失败。"""

    def __init__(self, message: str):
        super().__init__(message, code=422)


class NotFoundError(SkillError):
    """资源不存在。"""

    def __init__(self, message: str):
        super().__init__(message, code=404)


class SkillExecutionError(SkillError):
    """Skill 执行失败。"""

    def __init__(self, message: str):
        super().__init__(message, code=500)


class SchedulerError(SkillError):
    """调度失败。"""

    def __init__(self, message: str):
        super().__init__(message, code=500)


class NotSupportedError(SkillError):
    """未实现的能力。"""

    def __init__(self, message: str):
        super().__init__(message, code=501)