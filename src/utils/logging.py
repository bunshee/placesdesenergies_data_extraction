from __future__ import annotations

import os
from loguru import logger


def configure_logger() -> None:
    # Remove default handler to avoid duplicates if reconfigured
    logger.remove()
    level = os.getenv("LOG_LEVEL", "INFO")
    fmt = os.getenv(
        "LOG_FORMAT",
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>",
    )
    logger.add(
        lambda msg: print(msg, end=""),
        level=level,
        format=fmt,
        enqueue=True,
        backtrace=False,
        diagnose=False,
    )


__all__ = ["logger", "configure_logger"]
