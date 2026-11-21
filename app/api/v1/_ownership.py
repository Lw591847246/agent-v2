# app/api/v1/_ownership.py
"""产物文件（下载类接口）的归属判定。

目录约定（与各 service 的写入路径一致）
    会话级：  data/<root>/<session_id>/<file>      mindmap / prototype / ppt / word
    任务级：  data/storyboard/<task_id>/<file>     manifest.json 内带 session_id
    扁平任务级：data/custom_avatar/<task_id>_final.mp4

所有判定失败一律抛 404（不暴露"存在但无权"），日志记一条 warning。
"""
from __future__ import annotations

import json
import os
from typing import Optional

from app.api.deps import assert_session_owned
from app.application.conversation_service import conversation_service
from app.core.exceptions import NotFoundException
from app.core.logger import logger
from app.infrastructure.repositories.task_repo import task_repo
from app.utils.safe_path import data_root, ensure_under

SESSION_ROOTS = (
    "mindmap_output", "prototype_output", "ppt_output", "word_output",
)
STORYBOARD_ROOT = "storyboard"
FLAT_TASK_ROOTS = ("custom_avatar",)

_MEDIA_SUFFIXES = ("_final", "_silent", "_lip")


def _rel_under(path: str, root: str) -> Optional[str]:
    """path 在 root 下则返回相对路径（posix），否则 None。"""
    try:
        ensure_under(root, path)
    except Exception:
        return None
    rel = os.path.relpath(os.path.realpath(path), os.path.realpath(root))
    if rel == ".":
        return None
    return rel.replace("\\", "/")


def _manifest_session(task_id: str) -> str:
    manifest = os.path.join(data_root(STORYBOARD_ROOT), task_id, "manifest.json")
    try:
        with open(manifest, "r", encoding="utf-8") as f:
            return str(json.load(f).get("session_id") or "")
    except Exception:
        return ""


def task_id_from_media_name(base_name: str) -> str:
    """custom_avatar 扁平产物：<task_id>_final.mp4 / <task_id>_silent.mp4。"""
    stem = os.path.splitext(base_name)[0]
    for suf in _MEDIA_SUFFIXES:
        if stem.endswith(suf):
            return stem[: -len(suf)]
    return stem


def _assert_task_or_session(task_id: str, user_id: int) -> None:
    info = task_repo.get(task_id)
    if info:
        owner = int(info.user_id or 0)
        if user_id > 0 and owner == user_id:
            return
        if owner == 0 and info.session_id and conversation_service.is_owned(
            info.session_id, user_id
        ):
            return
    sid = _manifest_session(task_id)
    if sid:
        assert_session_owned(sid, user_id)
        return
    logger.warning(f"[Authz] 产物任务归属拒绝 task={task_id} uid={user_id}")
    raise NotFoundException("文件不存在")


def assert_storyboard_task(task_id: str, user_id: int) -> None:
    """storyboard 的目录名即 task_id。"""
    _assert_task_or_session(task_id, user_id)


def assert_file_owned(abs_path: str, user_id: int) -> str:
    """校验产物文件属于该用户；无法归类也视为不通过（fail-closed）。"""
    # 1) 会话级目录
    for name in SESSION_ROOTS:
        rel = _rel_under(abs_path, data_root(name))
        if rel is None:
            continue
        session_id = rel.split("/", 1)[0]
        assert_session_owned(session_id, user_id)
        return abs_path

    # 2) storyboard：<task_id>/...
    rel = _rel_under(abs_path, data_root(STORYBOARD_ROOT))
    if rel is not None:
        _assert_task_or_session(rel.split("/", 1)[0], user_id)
        return abs_path

    # 3) custom_avatar：<task_id>_final.mp4
    for name in FLAT_TASK_ROOTS:
        rel = _rel_under(abs_path, data_root(name))
        if rel is None:
            continue
        task_id = task_id_from_media_name(rel.split("/", 1)[0])
        if not task_id:
            break
        _assert_task_or_session(task_id, user_id)
        return abs_path

    logger.warning(f"[Authz] 产物路径无法归类，拒绝: {abs_path} uid={user_id}")
    raise NotFoundException("文件不存在")