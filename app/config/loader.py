"""分层配置加载器，对标 Nacos Config"""
import os
import re
import yaml
from pathlib import Path
from typing import Any, Dict

_PLACEHOLDER = re.compile(r"\$\{([^}]+)\}")


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
            return _PLACEHOLDER.sub(
                lambda m: os.getenv(m.group(1), m.group(0)), obj
            )
        return obj

    def get(self, key: str, default=None):
        value = self._config
        for k in key.split("."):
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value


config = ConfigLoader()