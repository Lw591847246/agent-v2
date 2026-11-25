# app/skills/code/workspace/store.py
from __future__ import annotations

import hashlib
import os
import shutil
from abc import ABC, abstractmethod
from typing import List

from app.core.logger import logger
from app.skills.code.workspace.contracts import FileMeta
from app.skills.code.workspace.validation import safe_join


class WorkspaceStore(ABC):
    @abstractmethod
    def save_file(self, uid: int, sid: str, relpath: str, content: bytes, scope: str = "all", module_id: str = "") -> FileMeta:
        ...

    @abstractmethod
    def read_file(self, uid: int, sid: str, storage_key: str) -> bytes:
        ...

    @abstractmethod
    def list_files(self, uid: int, sid: str, scope: str = "all", module_id: str = "") -> List[FileMeta]:
        ...

    @abstractmethod
    def clear(self, uid: int, sid: str) -> None:
        ...


class LocalFsWorkspaceStore(WorkspaceStore):
    """原始代码落本地 data/workspace/<uid>/<sid>/raw/...；Redis 只存元数据。"""

    def _root(self, uid: int, sid: str) -> str:
        return os.path.abspath(os.path.join("data", "workspace", str(uid), sid))

    def save_file(self, uid, sid, relpath, content, scope="all", module_id=""):
        root = self._root(uid, sid)
        target = safe_join(root, "raw", relpath)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as f:
            f.write(content)
        sha = hashlib.sha256(content).hexdigest()
        return FileMeta(
            relpath=relpath, scope=scope, module_id=module_id,
            size=len(content), sha256=sha,
            storage_key=os.path.relpath(target, root).replace("\\", "/"),
        )

    def read_file(self, uid, sid, storage_key):
        root = self._root(uid, sid)
        target = safe_join(root, storage_key)
        with open(target, "rb") as f:
            return f.read()

    def list_files(self, uid, sid, scope="all", module_id=""):
        root = self._root(uid, sid)
        raw = os.path.join(root, "raw")
        if not os.path.isdir(raw):
            return []
        out: List[FileMeta] = []
        for dirpath, _, files in os.walk(raw):
            for name in files:
                abs_path = os.path.join(dirpath, name)
                rel = os.path.relpath(abs_path, raw).replace("\\", "/")
                if scope == "module" and module_id:
                    if not rel.startswith(f"{module_id}/"):
                        continue
                try:
                    size = os.path.getsize(abs_path)
                    with open(abs_path, "rb") as f:
                        sha = hashlib.sha256(f.read()).hexdigest()
                except Exception:
                    continue
                out.append(FileMeta(
                    relpath=rel, scope=scope, module_id=module_id,
                    size=size, sha256=sha,
                    storage_key=os.path.relpath(abs_path, root).replace("\\", "/"),
                ))
        return out

    def clear(self, uid, sid):
        root = self._root(uid, sid)
        if os.path.isdir(root):
            shutil.rmtree(root, ignore_errors=True)
            logger.info(f"[CodeWorkspace] 清理本地目录 {root}")


workspace_store: WorkspaceStore = LocalFsWorkspaceStore()