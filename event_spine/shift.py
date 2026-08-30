"""Shift fold of the log. Disposable rows; TicketOpened still owns the clock."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from event_spine.events import Event
from event_spine.project import Ticket, project
from event_spine.stats import percentile


# Fixed emit order for ops bands (not alpha — afternoon must not beat midday).
_SHIFT_ORDER = ("morning", "midday", "afternoon")


@dataclass(frozen=True, slots=True)
class ShiftRow:
    shift: str
    tickets: int
    closed: int
    open: int
    revenue_cents: int
    dwell_p50_min: float | None


def _hour_of(source: object) -> int | None:
    """Pull a clock hour from a ticket, datetime, event, or int. Junk → None."""
    if source is None:
        return None
    if isinstance(source, bool):
        return None
    if isinstance(source, int):
        return source
    if isinstance(source, float):
        if source != source:  # NaN
            return None
        return int(source)
    if isinstance(source, Ticket):
        return source.opened_at.hour
    if isinstance(source, datetime):
        return source.hour
    if isinstance(source, Event):
        return source.occurred_at.hour
    opened = getattr(source, "opened_at", None)
    if isinstance(opened, datetime):
        return opened.hour
    occurred = getattr(source, "occurred_at", None)
    if isinstance(occurred, datetime):
        return occurred.hour
    if isinstance(source, str):
        text = source.strip()
        if not text:
            return None
        if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
            return int(text)
        try:
            return datetime.fromisoformat(text).hour
        except ValueError:
            return None
    return None


def shift_of(source: object) -> str:
    """Map a TicketOpened hour to morning, midday, or afternoon.

    Morning is 07:00–10:59, midday 11:00–13:59, afternoon 14:00–18:59
    (UTC). Accepts a Ticket (uses opened_at), a datetime, an Event, or an
    hour int. Hours outside 7–18 and anything unclassifiable stay empty
    so the renderer can print —.
    """
    hour = _hour_of(source)
    if hour is None:
        return ""
    if 7 <= hour <= 10:
        return "morning"
    if 11 <= hour <= 13:
        return "midday"
    if 14 <= hour <= 18:
        return "afternoon"
    return ""


def _shift_rank(shift: str) -> int:
    try:
        return _SHIFT_ORDER.index(shift)
    except ValueError:
        return len(_SHIFT_ORDER)


def by_shift(events: list[Event]) -> list[ShiftRow]:
    """Rebuild one row per shop-open shift from the ticket projection.

    Shift is classified from TicketOpened.occurred_at hour (UTC) via
    shift_of on each ticket. Revenue is the sum of closed ticket
    line-item totals (integer cents). dwell_p50_min is Hyndman-Fan
    type 7 over closed-ticket dwell minutes. Still-open tickets count
    toward tickets/open but not revenue or dwell. Sorted by
    revenue_cents desc, then morning / midday / afternoon (empty last).
    """
    tickets = project(events)
    by: dict[str, list[Ticket]] = {}
    for ticket in tickets.values():
        by.setdefault(shift_of(ticket), []).append(ticket)

    rows: list[ShiftRow] = []
    for shift, group in by.items():
        closed = [t for t in group if t.closed]
        dwells = [
            (t.closed_at - t.opened_at).total_seconds() / 60.0
            for t in closed
            if t.closed_at is not None
        ]
        rows.append(
            ShiftRow(
                shift=shift,
                tickets=len(group),
                closed=len(closed),
                open=len(group) - len(closed),
                revenue_cents=sum(t.total_cents for t in closed),
                dwell_p50_min=percentile(dwells, 0.50),
            )
        )
    rows.sort(key=lambda row: (-row.revenue_cents, _shift_rank(row.shift), row.shift))
    return rows
