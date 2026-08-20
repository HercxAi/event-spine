"""Append-only JSONL event store. The file is the log; we never rewrite a line."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path

from event_spine.events import Event, decode, encode


class JsonlEventStore:
    """Write events as one JSON object per line. Read them back in order."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, event: Event) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(encode(event))
            fh.write("\n")

    def append_many(self, events: Iterable[Event]) -> int:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        n = 0
        with self.path.open("a", encoding="utf-8") as fh:
            for event in events:
                fh.write(encode(event))
                fh.write("\n")
                n += 1
        return n

    def __iter__(self) -> Iterator[Event]:
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if line:
                    yield decode(line)

    def load(self) -> list[Event]:
        return list(self)
