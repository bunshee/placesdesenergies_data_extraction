import sys
from datetime import datetime
from pathlib import Path

from loguru import logger


class SingleLineConsoleHandler:
    """Custom handler that shows only the latest warning/error, updating in place."""

    def __init__(self):
        self.last_message_length = 0

    def write(self, message):
        """Write message, clearing the previous one."""
        # Get the terminal width to properly clear the line
        try:
            import shutil

            terminal_width = shutil.get_terminal_size().columns
        except Exception:
            terminal_width = 120

        # Clear the previous line by overwriting with spaces
        if self.last_message_length > 0:
            sys.stderr.write("\r" + " " * terminal_width + "\r")
            sys.stderr.flush()

        # Strip the message of newlines and extra whitespace
        clean_message = message.strip()

        if clean_message:
            # Write the new message
            sys.stderr.write("\r" + clean_message)
            sys.stderr.flush()
            self.last_message_length = len(clean_message)


def init_logger(
    log_level: str = "INFO",
    console_level: str | None = None,
):
    """
    Initializes the loguru logger with file output and optional console output.

    Args:
        log_level (str): Minimum logging level for file ('DEBUG', 'INFO', 'WARNING', 'ERROR').
        console_level (str | None): Minimum logging level for console. If None, console logging is disabled.

    Returns:
        logger: Configured logger instance
    """
    # Remove default logger
    logger.remove()

    # Create logs directory if it doesn't exist
    logs_dir = Path("./logs")
    logs_dir.mkdir(exist_ok=True)

    # Generate log filename with current date and time
    log_filename = logs_dir / f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log"

    # Add file handler - saves all logs (detailed)
    logger.add(
        log_filename,
        level=log_level.upper(),
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
        rotation="100 MB",  # Rotate if file gets too large
        retention="30 days",  # Keep logs for 30 days
        compression="zip",  # Compress rotated logs
    )

    # Add console handler only if requested
    if console_level:
        console_handler = SingleLineConsoleHandler()
        logger.add(
            console_handler.write,
            level=console_level.upper(),
            colorize=True,
            format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>\n",
        )

    logger.info(
        f"Logger initialized. File logging level: {log_level.upper()}, Console level: {console_level.upper() if console_level else 'DISABLED'}",
    )
    logger.info(f"Logs saved to: {log_filename}")

    return logger
