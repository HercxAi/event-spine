from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from event_spine.events import Event, EventType
from event_spine.store import JsonlEventStore


def _e(n: int) -> Event:
    return Event(
        event_id=f"e_{n}",
        type=EventType.TICKET_OPENED,
        occurred_at=datetime(2026, 3, 14, 7, n, tzinfo=UTC),
        ticket_id=f"t_{n}",
        payload={"bay": "1"},
    )


class StoreTests(unittest.TestCase):
    def test_append_then_load_in_order(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            store = JsonlEventStore(path)
            store.append(_e(1))
            store.append(_e(2))
            loaded = store.load()
            self.assertEqual([e.event_id for e in loaded], ["e_1", "e_2"])

    def test_append_never_rewrites_earlier_lines(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "events.jsonl"
            store = JsonlEventStore(path)
            store.append(_e(1))
            first = path.read_text(encoding="utf-8")
            store.append(_e(2))
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith(first))
            self.assertEqual(text.count("\n"), 2)

    def test_missing_file_is_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            store = JsonlEventStore(Path(tmp) / "nope.jsonl")
            self.assertEqual(store.load(), [])

    def test_append_many_creates_parents(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "a" / "b" / "events.jsonl"
            n = JsonlEventStore(path).append_many([_e(1), _e(2), _e(3)])
            self.assertEqual(n, 3)
            self.assertEqual(len(JsonlEventStore(path).load()), 3)
