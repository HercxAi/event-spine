"""Fold the log into tickets. The projection is disposable; the events are not."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from event_spine.events import Event, EventType


@dataclass(frozen=True, slots=True)
class LineItem:
    sku: str
    description: str
    qty: int
    unit_cents: int

    @property
    def ext_cents(self) -> int:
        return self.qty * self.unit_cents


@dataclass(frozen=True, slots=True)
class Payment:
    ok: bool
    method: str
    amount_cents: int
    at: datetime
    event_id: str
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class Ticket:
    ticket_id: str
    opened_at: datetime
    closed_at: datetime | None
    bay: str
    vehicle: str
    items: tuple[LineItem, ...]
    payments: tuple[Payment, ...]

    @property
    def total_cents(self) -> int:
        return sum(item.ext_cents for item in self.items)

    @property
    def paid(self) -> bool:
        captured = sum(p.amount_cents for p in self.payments if p.ok)
        return captured >= self.total_cents and self.total_cents >= 0

    @property
    def closed(self) -> bool:
        return self.closed_at is not None


def apply(ticket: Ticket | None, event: Event) -> Ticket:
    """Pure fold of one event onto a ticket (or None for TicketOpened)."""
    if event.type is EventType.TICKET_OPENED:
        return Ticket(
            ticket_id=event.ticket_id,
            opened_at=event.occurred_at,
            closed_at=None,
            bay=str(event.payload.get("bay", "")),
            vehicle=str(event.payload.get("vehicle", "")),
            items=(),
            payments=(),
        )
    if ticket is None:
        raise ValueError(f"{event.type.value} {event.event_id} has no TicketOpened")

    if event.type is EventType.LINE_ITEM_ADDED:
        item = LineItem(
            sku=str(event.payload["sku"]),
            description=str(event.payload.get("description", "")),
            qty=int(event.payload.get("qty", 1)),
            unit_cents=int(event.payload["unit_cents"]),
        )
        return replace(ticket, items=ticket.items + (item,))

    if event.type is EventType.PAYMENT_CAPTURED:
        payment = Payment(
            ok=True,
            method=str(event.payload.get("method", "")),
            amount_cents=int(event.payload["amount_cents"]),
            at=event.occurred_at,
            event_id=event.event_id,
        )
        return replace(ticket, payments=ticket.payments + (payment,))

    if event.type is EventType.PAYMENT_FAILED:
        payment = Payment(
            ok=False,
            method=str(event.payload.get("method", "")),
            amount_cents=int(event.payload.get("amount_cents", 0)),
            at=event.occurred_at,
            event_id=event.event_id,
            reason=str(event.payload["reason"]) if event.payload.get("reason") else None,
        )
        return replace(ticket, payments=ticket.payments + (payment,))

    if event.type is EventType.TICKET_CLOSED:
        return replace(ticket, closed_at=event.occurred_at)

    raise ValueError(f"unknown event type {event.type}")


def project(events: list[Event]) -> dict[str, Ticket]:
    tickets: dict[str, Ticket] = {}
    for event in events:
        current = tickets.get(event.ticket_id)
        tickets[event.ticket_id] = apply(current, event)
    return tickets


def closed_in_order(tickets: dict[str, Ticket]) -> list[Ticket]:
    closed = [t for t in tickets.values() if t.closed]
    closed.sort(key=lambda t: (t.closed_at or t.opened_at, t.ticket_id))
    return closed
