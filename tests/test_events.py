from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime

from event_spine.events import Event, EventType, decode, encode


class EventCodecTests(unittest.TestCase):
    def test_roundtrip_preserves_fields(self) -> None:
        event = Event(
            event_id="e_0001",
            type=EventType.LINE_ITEM_ADDED,
            occurred_at=datetime(2026, 3, 14, 8, 15, 2, tzinfo=UTC),
            ticket_id="t_001",
            payload={"sku": "OIL-FS", "qty": 1, "unit_cents": 8499},
        )
        again = decode(encode(event))
        self.assertEqual(again.event_id, event.event_id)
        self.assertEqual(again.type, EventType.LINE_ITEM_ADDED)
        self.assertEqual(again.ticket_id, "t_001")
        self.assertEqual(again.payload["unit_cents"], 8499)
        self.assertEqual(again.occurred_at, event.occurred_at)

    def test_json_is_one_line(self) -> None:
        event = Event(
            "e_1",
            EventType.TICKET_OPENED,
            datetime(2026, 3, 14, 7, 0, tzinfo=UTC),
            "t_1",
            {"bay": "2"},
        )
        raw = encode(event)
        self.assertNotIn("\n", raw)
        self.assertEqual(json.loads(raw)["type"], "TicketOpened")

    def test_naive_timestamp_becomes_utc(self) -> None:
        raw = (
            '{"event_id":"e","type":"TicketClosed",'
            '"occurred_at":"2026-03-14T12:00:00","ticket_id":"t","payload":{}}'
        )
        event = decode(raw)
        self.assertEqual(event.occurred_at.tzinfo, UTC)

    def test_unknown_type_raises(self) -> None:
        with self.assertRaises(ValueError):
            Event.from_dict(
                {
                    "event_id": "e",
                    "type": "TicketRefunded",
                    "occurred_at": "2026-03-14T12:00:00+00:00",
                    "ticket_id": "t",
                    "payload": {},
                }
            )

    def test_payload_is_not_shared_mutable_state(self) -> None:
        event = Event(
            "e",
            EventType.TICKET_OPENED,
            datetime(2026, 3, 14, 7, 0, tzinfo=UTC),
            "t",
            {"bay": "1"},
        )
        with self.assertRaises(TypeError):
            event.payload["bay"] = "hacked"  # type: ignore[index]
