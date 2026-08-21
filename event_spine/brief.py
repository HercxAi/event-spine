"""One-page daily ops brief. Walk the log; throw the view away."""

from __future__ import annotations

from dataclasses import dataclass

from event_spine.events import Event, EventType
from event_spine.project import Ticket, project
from event_spine.stats import summarize


@dataclass(frozen=True, slots=True)
class DayBrief:
    events: int
    tickets_opened: int
    tickets_closed: int
    payments_captured: int
    payments_failed: int
    revenue_cents: int
    leftover: tuple[Ticket, ...]
    detector_hits: tuple[tuple[str, int], ...]


def from_log(events: list[Event]) -> DayBrief:
    """Rebuild the day's ops numbers from the append-only facts.

    Revenue is the sum of PaymentCaptured.amount_cents (integer cents).
    Failed charges do not count. Leftover tickets are still open after
    the last event. Detector hits reuse the existing detect/stats fold.
    """
    tickets = project(events)
    leftover = tuple(
        sorted(
            (ticket for ticket in tickets.values() if not ticket.closed),
            key=lambda ticket: (ticket.opened_at, ticket.ticket_id),
        )
    )
    captured = [e for e in events if e.type is EventType.PAYMENT_CAPTURED]
    failed = [e for e in events if e.type is EventType.PAYMENT_FAILED]
    revenue = sum(int(e.payload.get("amount_cents", 0)) for e in captured)
    return DayBrief(
        events=len(events),
        tickets_opened=sum(1 for e in events if e.type is EventType.TICKET_OPENED),
        tickets_closed=sum(1 for e in events if e.type is EventType.TICKET_CLOSED),
        payments_captured=len(captured),
        payments_failed=len(failed),
        revenue_cents=revenue,
        leftover=leftover,
        detector_hits=summarize(events).detector_hits,
    )
