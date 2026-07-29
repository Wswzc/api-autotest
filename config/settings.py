"""配置加载

设计要点：
  · 配置与代码分离，用例里不出现任何 URL、账号、阈值的字面量
  · 单例：一次会话内只解析一次配置文件
  · 环境由命令行参数决定，支持 local / test 等多套环境
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config" / "config.yaml"


class Settings:
    _instance: "Settings | None" = None

    def __new__(cls, env: str | None = None) -> "Settings":
        if cls._instance is None:
            instance = super().__new__(cls)
            instance._load(env)
            cls._instance = instance
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """仅供测试框架自身使用：允许在同一进程内切换环境"""
        cls._instance = None

    def _load(self, env: str | None) -> None:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            raw: dict[str, Any] = yaml.safe_load(f)

        self.env: str = env or raw.get("default_env", "local")
        if self.env not in raw["envs"]:
            raise ValueError(f"配置文件中不存在环境 {self.env!r}，可选：{list(raw['envs'])}")

        cfg: dict[str, Any] = raw["envs"][self.env]
        self.base_url: str = cfg["base_url"].rstrip("/")
        self.api_prefix: str = cfg.get("api_prefix", "").rstrip("/")
        self.timeout: int = cfg.get("timeout", 10)
        self.auto_start_mock: bool = cfg.get("auto_start_mock", False)
        self.account: dict[str, str] = cfg.get("account", {})
        self.account_b: dict[str, str] = cfg.get("account_b", {})
        self.thresholds: dict[str, int] = cfg.get("thresholds", {})

    @property
    def api_base(self) -> str:
        return f"{self.base_url}{self.api_prefix}"

    @property
    def max_response_ms(self) -> int:
        return self.thresholds.get("response_time_ms", 1000)

    def __repr__(self) -> str:
        return f"<Settings env={self.env} base_url={self.base_url}>"
