# app/skills/code/workspace/contracts.py
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class FileMeta(BaseModel):
    relpath: str
    scope: str = "all"              # all | module
    module_id: str = ""
    language: str = ""
    size: int = 0
    sha256: str = ""
    storage_key: str = ""
    ignored: bool = False
    reason: str = ""


class ChunkMeta(BaseModel):
    chunk_id: str
    relpath: str
    scope: str = "all"
    module_id: str = ""
    language: str = ""
    start_line: int = 1
    end_line: int = 1
    content: str = ""
    token_est: int = 0


class MapChunkSummary(BaseModel):
    chunk_id: str
    file: str
    language: str = ""
    purpose: str = ""
    symbols: List[str] = Field(default_factory=list)
    public_api: List[str] = Field(default_factory=list)
    imports: List[str] = Field(default_factory=list)
    side_effects: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    unknowns: List[str] = Field(default_factory=list)
    citations: List[str] = Field(default_factory=list)


class CodeMap(BaseModel):
    session_id: str
    scope: str = "all"
    module_id: str = ""
    generated_at: int = 0
    file_count: int = 0
    chunk_count: int = 0
    overview: str = ""
    modules: List[Dict[str, Any]] = Field(default_factory=list)
    key_files: List[Dict[str, Any]] = Field(default_factory=list)
    dependencies: List[Dict[str, Any]] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    unknowns: List[str] = Field(default_factory=list)
    citations: List[str] = Field(default_factory=list)


class ScanProgress(BaseModel):
    job_id: str
    session_id: str
    scope: str = "all"
    module_id: str = ""
    status: str = "created"          # created|queued|scanning|mapping|reducing|done|failed|cancelled
    phase: str = ""
    processed: int = 0
    total: int = 0
    current: str = ""
    error: str = ""
    started_at: int = 0
    updated_at: int = 0
    finished_at: int = 0


class ContextPack(BaseModel):
    session_id: str
    code_map: str = ""
    module_map: str = ""
    relevant_chunks: List[Dict[str, Any]] = Field(default_factory=list)
    recent_turns: List[Dict[str, Any]] = Field(default_factory=list)
    summary: str = ""
    pins: List[str] = Field(default_factory=list)
    token_est: int = 0