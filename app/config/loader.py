# app/config/loader.py
"""分层配置加载器（application.yml + application-{env}.yml + 环境变量占位）。

本版修复（对应架构复核 P0-3）
---------------------------
1. **在加载配置之前** `load_dotenv()`：原来只有 `settings.py` 调了 dotenv，
   而 `config` 是模块级单例——如果 `core.logger` 等模块先间接 import 了 loader，
   `.env` 里的值对 loader 不可见（顺序依赖，极难排查）。
2. 占位语法支持默认值：`${VAR}` 与 `${VAR:-default}`；
   变量缺失且无默认值时**保留原样并告警一次**，避免静默拿到空串。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, Set

import yaml

# 必须在任何 config 读取之前加载 .env
_BASE_DIR = Path(__file__).resolve().parent.parent.parent
try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv(_BASE_DIR / ".env", override=False)
except Exception:  # python-dotenv 未安装时退回纯环境变量
    pass

# ${VAR} 或 ${VAR:-default}
_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

_missing_warned: Set[str] = set()


class ConfigLoader:

    def __init__(self, config_dir: Path = None):
        self.config_dir = config_dir or Path(__file__).parent
        self._config: Dict[str, Any] = {}
        self._load()

    def _load(self):
        self._merge("application.yml")
        env = os.getenv("APP_ENV", "dev")
        self._merge(f"application-{env}.yml")
        self._config = self._resolve(self._config)

    def _merge(self, filename: str):
        path = self.config_dir / filename
        if not path.exists():
            return
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        self._config = self._deep_merge(self._config, data)

    @staticmethod
    def _deep_merge(base: dict, override: dict) -> dict:
        result = base.copy()
        for k, v in override.items():
            if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                result[k] = ConfigLoader._deep_merge(result[k], v)
            else:
                result[k] = v
        return result

    def _resolve(self, obj):
        if isinstance(obj, dict):
            return {k: self._resolve(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._resolve(i) for i in obj]
        if isinstance(obj, str):
            return _PLACEHOLDER.sub(self._sub_env, obj)
        return obj

    @staticmethod
    def _sub_env(m: "re.Match") -> str:
        name, default = m.group(1), m.group(2)
        val = os.getenv(name)
        if val is not None:
            return val
        if default is not None:
            return default
        if name not in _missing_warned:
            _missing_warned.add(name)
            try:
                from app.core.logger import logger
                logger.warning(
                    f"[Config] 环境变量 {name} 未设置且无默认值，占位符将原样保留"
                )
            except Exception:
                pass
        return m.group(0)

    def get(self, key: str, default=None):
        value = self._config
        for k in key.split("."):
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value

    def all(self) -> Dict[str, Any]:
        """只读快照（调试用）。"""
        return self._config


config = ConfigLoader()