import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

LOGGER = logging.getLogger("camp_scanner")


class ColorFormatter(logging.Formatter):
    """Add ANSI colors to console records without contaminating log files."""

    COLORS = {
        logging.DEBUG: "\033[36m",
        logging.INFO: "\033[32m",
        logging.WARNING: "\033[33m",
        logging.ERROR: "\033[31m",
        logging.CRITICAL: "\033[1;31m",
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        color = self.COLORS.get(record.levelno, "")
        return f"{color}{formatted}{self.RESET}" if color else formatted


def configure_logging(log_file: Path, color: bool = True) -> None:
    """Log to the terminal and rotate daily files retained for seven days."""
    try:
        log_file = log_file.expanduser().resolve()
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = TimedRotatingFileHandler(
            log_file,
            when="midnight",
            interval=1,
            backupCount=7,
            encoding="utf-8",
        )
    except OSError as exc:
        raise RuntimeError(f"Cannot open log file {log_file}: {exc}") from exc

    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s [%(threadName)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S %z",
        )
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_format = "%(asctime)s %(levelname)-8s [%(threadName)s] %(message)s"
    formatter = ColorFormatter if color else logging.Formatter
    console_handler.setFormatter(
        formatter(console_format, datefmt="%Y-%m-%d %H:%M:%S %z")
    )

    LOGGER.setLevel(logging.DEBUG)
    LOGGER.handlers.clear()
    LOGGER.addHandler(file_handler)
    LOGGER.addHandler(console_handler)
    LOGGER.propagate = False
    LOGGER.debug("Logging initialized at %s", log_file)
