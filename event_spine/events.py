"""Facts on the log. Nothing here is a mutable ticket row."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping


class EventType(str, Enum):
    TICKET_OPENED = "TicketOpened"
    LINE_ITEM_ADDED = "LineItemAdded"
    PAYMENT_CAPTURED = "PaymentCaptured"
    PAYMENT_FAILED = "PaymentFailed"
    TICKET_CLOSED = "TicketClosed"


PAYMENT_TYPES = frozenset({EventType.PAYMENT_CAPTURED, EventType.PAYMENT_FAILED})


def parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


@dataclass(frozen=True, slots=True)
class Event:
    event_id: str
    type: EventType
    occurred_at: datetime
    ticket_id: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "type": self.type.value,
            "occurred_at": self.occurred_at.isoformat(),
            "ticket_id": self.ticket_id,
            "payload": dict(self.payload),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Event:
        return cls(
            event_id=str(data["event_id"]),
            type=EventType(data["type"]),
            occurred_at=parse_dt(str(data["occurred_at"])),
            ticket_id=str(data["ticket_id"]),
            payload=dict(data.get("payload") or {}),
        )


def encode(event: Event) -> str:
    return json.dumps(event.to_dict(), separators=(",", ":"), ensure_ascii=False)


def decode(line: str) -> Event:
    return Event.from_dict(json.loads(line))
