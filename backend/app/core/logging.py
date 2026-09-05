import json
import logging
import os
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


class JsonFormatter(logging.Formatter):
    """Small dependency-free JSON formatter suitable for container logs."""

    _reserved = set(logging.makeLogRecord({}).__dict__)

    def format(self, record: logging.LogRecord) -> str:
        service_name = os.getenv("SERVICE_NAME", "backend")
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": service_name,
        }
        for key, value in record.__dict__.items():
            if key not in self._reserved and key not in {"message", "asctime"}:
                try:
                    json.dumps(value)
                    payload[key] = value
                except (TypeError, ValueError):
                    payload[key] = str(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    formatter = JsonFormatter()
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    file_error: OSError | None = None
    log_dir = os.getenv("LOG_DIR", "").strip()
    service_name = os.getenv("SERVICE_NAME", "backend").strip()
    if log_dir and service_name:
        try:
            directory = Path(log_dir)
            directory.mkdir(parents=True, exist_ok=True)
            handlers.append(
                RotatingFileHandler(
                    directory / f"{service_name}.log",
                    maxBytes=10 * 1024 * 1024,
                    backupCount=5,
                    encoding="utf-8",
                )
            )
        except OSError as exc:
            # Container stdout remains available if the shared log volume is unavailable.
            file_error = exc
    for handler in handlers:
        handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(level.upper())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        container_logger = logging.getLogger(logger_name)
        container_logger.handlers.clear()
        container_logger.propagate = True
    if file_error:
        logging.getLogger(__name__).warning(
            "Shared runtime log file is unavailable",
            extra={"error": str(file_error)},
        )
