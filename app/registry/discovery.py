# app/registry/discovery.py
"""Skill 自动发现。"""
from __future__ import annotations

import importlib
import pkgutil

from app.contracts.skill import BaseSkill
from app.core.logger import logger
from app.registry.skill_registry import registry


def auto_discover_skills(package_name: str = "app.skills") -> int:
    """
    扫描 package 下所有子包，导入 skill.py，找模块级 `skill` 实例并注册。
    """
    package = importlib.import_module(package_name)
    count = 0
    for _importer, modname, ispkg in pkgutil.iter_modules(
        package.__path__, prefix=package.__name__ + "."
    ):
        if not ispkg or modname.endswith("._template"):
            continue
        skill_module_name = f"{modname}.skill"
        try:
            mod = importlib.import_module(skill_module_name)
        except ModuleNotFoundError:
            logger.warning(f"[Discovery] {skill_module_name} 不存在，跳过")
            continue
        except Exception as e:
            logger.error(f"[Discovery] 导入 {skill_module_name} 失败: {e}", exc_info=True)
            continue

        skill = getattr(mod, "skill", None)
        if not isinstance(skill, BaseSkill):
            logger.warning(f"[Discovery] {skill_module_name} 未导出 BaseSkill 实例")
            continue

        registry.register(skill)
        count += 1

    logger.info(f"[Discovery] 共发现 {count} 个 Skill")
    return count