"""Shared utilities for the maptoposter package (no internal imports)."""

import json
from datetime import UTC, datetime
from typing import Any


class CacheError(Exception):
    """Raised when a cache operation fails."""


class StatusReporter:
    """Lightweight status/event logger with optional JSON output."""

    def __init__(self, json_mode: bool = False, debug: bool = False) -> None:
        self.json_mode = json_mode
        self.debug = debug

    def emit(self, event: str, message: str | None = None, **extra: Any) -> None:
        payload = {
            "event": event,
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            **extra,
        }
        if message is not None:
            payload["message"] = message
        if self.json_mode:
            print(json.dumps(payload, ensure_ascii=False))
        elif message is not None:
            print(message)

    def debug_log(self, message: str, **extra: Any) -> None:
        """Emit a debug-level message only when debug mode is active."""
        if not self.debug:
            return
        self.emit("debug", f"[DEBUG] {message}", **extra)


def _emit_status(
    status_reporter: StatusReporter | None,
    event: str,
    message: str | None = None,
    **extra: Any,
) -> None:
    if status_reporter is not None:
        status_reporter.emit(event, message, **extra)
    elif message is not None:
        print(message)
