"""Append-only structured run events."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Event:
    """One monotonically sequenced job event."""

    sequence: int
    created_utc: str
    level: str
    type: str
    stage: str | None
    message: str
    data: dict[str, Any]


class EventRecorder:
    """Write JSONL events suitable for CLI output, web polling, or SSE replay."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._sequence = 0

    def emit(
        self,
        event_type: str,
        message: str,
        *,
        stage: str | None = None,
        level: str = "info",
        data: dict[str, Any] | None = None,
    ) -> Event:
        self._sequence += 1
        event = Event(
            sequence=self._sequence,
            created_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            level=level,
            type=event_type,
            stage=stage,
            message=message,
            data=data or {},
        )
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(
                json.dumps(
                    asdict(event),
                    sort_keys=True,
                    ensure_ascii=False,
                    allow_nan=False,
                )
                + "\n"
            )
            stream.flush()
        return event
