# app/registry/__init__.py
"""注册中心。"""
from app.registry.discovery import auto_discover_skills
from app.registry.skill_registry import SkillRegistry, registry

__all__ = ["SkillRegistry", "registry", "auto_discover_skills"]