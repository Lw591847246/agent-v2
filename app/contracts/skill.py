# app/contracts/skill.py
"""Skill 基类：所有能力的唯一实现入口。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from app.contracts.action import ActionRequest
from app.contracts.result import Result


class BaseSkill(ABC):
    # ========== 元数据（子类覆盖） ==========
    name: str = ""
    description: str = ""
    schema: dict = {"type": "object", "properties": {}, "required": []}
    intent: str | None = None
    category: str = "general"

    # 执行元数据
    sync: bool = True
    returns: list[str] = ["text"]
    requires_attachments: list[str] = []
    produces_outputs: bool = False
    visible_to_l2: bool = True
    visible_to_planner: bool = True

    # ========== 业务实现（必须实现 execute） ==========
    @abstractmethod
    def execute(self, **kwargs) -> Result:
        """同步执行。"""
        ...

    def stream(self, **kwargs) -> Iterator[str]:
        """流式执行（可选）。默认降级为一次性返回。"""
        result = self.execute(**kwargs)
        text = result.as_text()
        if text:
            yield text

    def on_action(self, action: ActionRequest) -> Result:
        """卡片点击（可选）。"""
        raise NotImplementedError(f"Skill {self.name} does not support action")

    # ========== 元数据导出 ==========
    def to_fc_tool(self) -> dict:
        """导出为 OpenAI Function Calling 工具格式。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.schema,
            },
        }

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name} intent={self.intent}>"