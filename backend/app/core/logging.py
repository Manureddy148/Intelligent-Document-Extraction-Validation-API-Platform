import logging
import sys

_CONFIGURED = False


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging once for the whole application."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )

    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers = [handler]

    # Quiet noisy third-party loggers unless the app itself is in DEBUG.
    if level.upper() != "DEBUG":
        logging.getLogger("multipart").setLevel(logging.WARNING)
        logging.getLogger("PIL").setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
