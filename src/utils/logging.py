import sys

from loguru import logger


def init_logger(log_level: str = "INFO"):
    """
    Initializes the loguru logger to output only to the console (sys.stderr).

    Args:
        log_level (str): Minimum logging level ('DEBUG', 'INFO', 'WARNING', 'ERROR').

    Returns:
        logger: Configured logger instance
    """
    logger.remove()
    logger.add(
        sys.stderr,
        level=log_level.upper(),
        colorize=True,
        format="<green>{time:HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    )

    logger.info(
        f"Logger initialized. Logging level: {log_level.upper()}. (Console only)"
    )
    return logger
