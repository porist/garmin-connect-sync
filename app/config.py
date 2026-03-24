import os
import shutil
import sys
from pathlib import Path
from typing import Optional

import yaml


class Config:
    _instance: Optional["Config"] = None

    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = self._resolve_config_path(config_path)
        self._load()

    def _resolve_config_path(self, config_path: str) -> Path:
        """Resolve config file path, handling PyInstaller bundle paths."""
        p = Path(config_path)
        if p.exists():
            return p
        # If running as PyInstaller bundle, check in _MEIPASS (internal folder)
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            bundled_config = Path(sys._MEIPASS) / config_path
            if bundled_config.exists():
                return bundled_config
        return p

    def _load(self):
        if not self.config_path.exists():
            # 从模板复制
            example_path = Path(__file__).parent.parent / "config.yaml.example"
            if example_path.exists():
                shutil.copy(example_path, self.config_path)
            else:
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
    def garmin_token_file(self) -> Optional[str]:
        """Token 文件路径，默认 ~/.garminconnect/token.json"""
        return self._data.get("garmin", {}).get("token_file")

    @property
    def garmin_login_off_peak_only(self) -> bool:
        """是否仅在非高峰时段登录"""
        return self._data.get("garmin", {}).get("login_off_peak_only", False)

    @property
    def garmin_login_max_retries(self) -> int:
        """登录最大重试次数"""
        return self._data.get("garmin", {}).get("login_max_retries", 10)

    @property
    def garmin_login_initial_retry_delay(self) -> float:
        """登录初始重试延迟（秒）"""
        return self._data.get("garmin", {}).get("login_initial_retry_delay", 30.0)

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

    # === Rate Limit 配置 ===

    @property
    def rate_limit_request_delay_seconds(self) -> float:
        """请求间延迟（秒）"""
        return self._data.get("rate_limit", {}).get("request_delay_seconds", 0.5)

    @property
    def rate_limit_max_retries(self) -> int:
        """最大重试次数"""
        return self._data.get("rate_limit", {}).get("max_retries", 5)

    @property
    def rate_limit_initial_retry_delay(self) -> float:
        """初始重试延迟（秒）"""
        return self._data.get("rate_limit", {}).get("initial_retry_delay", 1.0)

    @property
    def rate_limit_max_retry_delay(self) -> float:
        """最大重试延迟（秒）"""
        return self._data.get("rate_limit", {}).get("max_retry_delay", 300.0)

    @property
    def rate_limit_backoff_factor(self) -> float:
        """退避因子"""
        return self._data.get("rate_limit", {}).get("backoff_factor", 2.0)

    @property
    def rate_limit_jitter(self) -> bool:
        """是否添加随机抖动"""
        return self._data.get("rate_limit", {}).get("jitter", True)

    @property
    def rate_limit_off_peak_hours(self) -> list:
        """避开的高峰时段（小时列表）"""
        return self._data.get("rate_limit", {}).get("off_peak_hours", [])

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
        save_path = self.config_path
        # 如果运行在 PyInstaller bundle 中，或者目标路径不可写，则保存到当前工作目录
        is_bundled = getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")
        if is_bundled or not os.access(save_path, os.W_OK):
            save_path = Path("config.yaml")
            if self.config_path.exists() and self.config_path.resolve() != save_path.resolve():
                shutil.copy(self.config_path, save_path)
            self.config_path = save_path
        with open(save_path, "w", encoding="utf-8") as f:
            yaml.dump(self._data, f, allow_unicode=True, default_flow_style=False)

    def get_all(self):
        """获取所有配置"""
        return self._data
