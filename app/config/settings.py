from pathlib import Path
from dotenv import load_dotenv

# 项目根目录（settings.py 往上 3 层）
BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")


"""强类型配置"""
from dataclasses import dataclass, field
from typing import List, Dict
from app.config.loader import config


@dataclass(frozen=True)
class AppSettings:
    name: str = config.get("app.name")
    version: str = config.get("app.version")
    env: str = config.get("app.env")


@dataclass(frozen=True)
class ServerSettings:
    host: str = config.get("server.host")
    port: int = config.get("server.port")
    workers: int = config.get("server.workers")


@dataclass(frozen=True)
class AuthSettings:
    white_list: List[str] = field(
        default_factory=lambda: config.get("auth.white_list", [])
    )


@dataclass(frozen=True)
class FileSettings:
    max_file_tokens: int = config.get("file.max_file_tokens")
    max_file_size_mb: int = config.get("file.max_file_size_mb")
    allowed_extensions: List[str] = field(
        default_factory=lambda: config.get("file.allowed_extensions", [])
    )


@dataclass(frozen=True)
class VectorStoreSettings:
    path: str = config.get("vector_store.path")
    collection: str = config.get("vector_store.collection")
    chunk_size: int = config.get("vector_store.chunk_size")
    chunk_overlap: int = config.get("vector_store.chunk_overlap")
    top_k: int = config.get("vector_store.top_k")


app_settings = AppSettings()
server_settings = ServerSettings()
auth_settings = AuthSettings()
file_settings = FileSettings()
vector_store_settings = VectorStoreSettings()