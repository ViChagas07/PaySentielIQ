# ============================================================
# PaySentinelIQ — Structured Logging (Fase 4)
# ============================================================

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

from app.observability.correlation import get_correlation


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ctx = get_correlation()
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "pipeline_id": ctx.pipeline_id or "",
            "trace_id": ctx.trace_id or "",
            "request_id": ctx.request_id or "",
            "tenant_id": ctx.tenant_id or "",
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        # Structured fields passed via logger.info("...", extra={...}) — e.g.
        # event_id, queue, duration_ms, retry_count from messaging consumers.
        _STANDARD_ATTRS = frozenset(
            logging.LogRecord("", 0, "", 0, "", (), None).__dict__
        )
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                log_entry[key] = value
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = str(record.exc_info[1])
            log_entry["stacktrace"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False, default=str)


class StructuredLogger:
    @staticmethod
    def setup(level: str = "INFO") -> None:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JSONFormatter())
        root = logging.getLogger()
        root.handlers.clear()
        root.addHandler(handler)
        root.setLevel(getattr(logging, level.upper(), logging.INFO))

    @staticmethod
    def get(name: str) -> logging.Logger:
        return logging.getLogger(name)


def get_logger(name: str) -> logging.Logger:
    return StructuredLogger.get(name)
