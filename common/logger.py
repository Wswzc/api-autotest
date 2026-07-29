"""日志

控制台只输出关键信息保证可读性，文件里保留 DEBUG 级别的完整请求响应，
用于事后排查"昨晚定时任务为什么红了"。
"""
from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logger.remove()
logger.add(
    sys.stderr,
    level="INFO",
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}",
)
logger.add(
    LOG_DIR / "run_{time:YYYY-MM-DD}.log",
    level="DEBUG",
    rotation="10 MB",
    retention="7 days",
    encoding="utf-8",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <7} | {name}:{line} | {message}",
)

__all__ = ["logger"]
