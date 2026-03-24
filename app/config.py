import os
from pathlib import Path
from typing import Optional

import yaml


class Config:
    _instance: Optional["Config"] = None

    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = Path(config_path)
        self._load()

    def _load(self):
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")

        with open(self.config_path, "r", encoding="utf-8") as f:
            self._data = yaml.safe_load(f)

    @classmethod
    def get_instance(cls, config_path: str = "config.yaml") -> "Config":
        if cls._instance is None:
            cls._instance = cls(config_path)
        return cls._instance

    @property
    def garmin_email(self) -> str:
        return self._data["garmin"]["email"]

    @property
    def garmin_password(self) -> str:
        return self._data["garmin"]["password"]

    @property
    def garmin_mfa_code(self) -> Optional[str]:
        return self._data["garmin"].get("mfa_code")

    @property
    def garmin_timeout(self) -> int:
        return self._data.get("garmin", {}).get("timeout", 10)

    @property
    def database_path(self) -> Path:
        db_path = self._data["database"]["path"]
        if not db_path.startswith("/"):
            # Relative path - resolve from project root
            project_root = Path(__file__).parent.parent
            db_path = project_root / db_path
        return Path(db_path)

    @property
    def sync_interval_hours(self) -> int:
        return self._data.get("scheduler", {}).get("sync_interval_hours", 6)

    @property
    def cron_expression(self) -> Optional[str]:
        return self._data.get("scheduler", {}).get("cron")

    def get(self, key: str):
        """获取配置项，支持点号分隔的路径如 'garmin.email'"""
        parts = key.split(".")
        value = self._data
        for part in parts:
            if isinstance(value, dict):
                value = value.get(part)
            else:
                return None
        return value

    def set(self, key: str, value):
        """设置配置项，支持点号分隔的路径如 'garmin.email'"""
        parts = key.split(".")
        target = self._data
        for part in parts[:-1]:
            if part not in target:
                target[part] = {}
            target = target[part]
        target[parts[-1]] = value

    def save(self):
        """保存配置到文件"""
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.dump(self._data, f, allow_unicode=True, default_flow_style=False)

    def get_all(self):
        """获取所有配置"""
        return self._data
