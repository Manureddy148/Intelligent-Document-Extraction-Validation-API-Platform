import contextvars
import logging
import sys

_CONFIGURED = False

# Set per request by the middleware so every log line emitted while handling a
# request can be tied back to it - without threading an id through every call.
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging once for the whole application."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)s | %(request_id)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers = [handler]

    # Quiet noisy third-party loggers unless the app itself is in DEBUG.
    if level.upper() != "DEBUG":
        logging.getLogger("multipart").setLevel(logging.WARNING)
        logging.getLogger("PIL").setLevel(logging.WARNING)
        logging.getLogger("pdfminer").setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
