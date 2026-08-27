"""Bay fold of the log. Disposable rows; TicketOpened still owns the bay."""

from __future__ import annotations

from dataclasses import dataclass

from event_spine.project import Ticket, project
from event_spine.stats import percentile
from event_spine.events import Event


@dataclass(frozen=True, slots=True)
class BayRow:
    bay: str
    tickets: int
    closed: int
    open: int
    revenue_cents: int
    dwell_p50_min: float | None


def by_bay(events: list[Event]) -> list[BayRow]:
    """Rebuild one row per bay from the ticket projection.

    Revenue is the sum of closed ticket line-item totals (integer cents).
    dwell_p50_min is Hyndman-Fan type 7 over closed-ticket dwell minutes.
    Still-open tickets count toward tickets/open but not revenue or dwell.
    Sorted by revenue_cents desc, then bay.
    """
    tickets = project(events)
    by: dict[str, list[Ticket]] = {}
    for ticket in tickets.values():
        by.setdefault(ticket.bay, []).append(ticket)

    rows: list[BayRow] = []
    for bay, group in by.items():
        closed = [t for t in group if t.closed]
        dwells = [
            (t.closed_at - t.opened_at).total_seconds() / 60.0
            for t in closed
            if t.closed_at is not None
        ]
        rows.append(
            BayRow(
                bay=bay,
                tickets=len(group),
                closed=len(closed),
                open=len(group) - len(closed),
                revenue_cents=sum(t.total_cents for t in closed),
                dwell_p50_min=percentile(dwells, 0.50),
            )
        )
    rows.sort(key=lambda row: (-row.revenue_cents, row.bay))
    return rows
