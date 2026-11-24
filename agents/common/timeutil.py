"""Time helpers shared by agents, monitoring and services."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def utcnow() -> datetime:
    return datetime.now(UTC)


def parse_ts(value: Any, default: datetime | None = None) -> datetime:
    """Parse ISO-8601 strings, epoch seconds/milliseconds or datetimes into aware UTC datetimes."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = value / 1000.0 if value > 1e11 else float(value)
        if value > 1e17:  # nanoseconds (OTLP)
            seconds = value / 1e9
        return datetime.fromtimestamp(seconds, UTC)
    if isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            try:
                return parse_ts(float(text))
            except ValueError:
                pass
    return default or utcnow()


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()
