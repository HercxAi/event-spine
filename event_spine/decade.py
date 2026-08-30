"""Decade fold of the log. Disposable rows; TicketOpened still owns the vehicle."""

from __future__ import annotations

from dataclasses import dataclass

from event_spine.events import Event
from event_spine.project import Ticket, project
from event_spine.stats import percentile
from event_spine.year import year_of


@dataclass(frozen=True, slots=True)
class DecadeRow:
    decade: str
    tickets: int
    closed: int
    open: int
    revenue_cents: int
    dwell_p50_min: float | None


def decade_of(vehicle: str) -> str:
    """Map a '2018 Honda Civic' style plate to a model-year decade.

    A leading four-digit year floors to the decade label (2018 → 2010s,
    2022 → 2020s). Missing or unparseable years stay empty so the
    renderer can print —.
    """
    year = year_of(vehicle)
    if not year:
        return ""
    floor = (int(year) // 10) * 10
    return f"{floor}s"


def _decade_rank(decade: str) -> int:
    """Newer decades first; empty / junk last."""
    if decade.endswith("s") and decade[:-1].isdigit():
        return -int(decade[:-1])
    return 0


def by_decade(events: list[Event]) -> list[DecadeRow]:
    """Rebuild one row per vehicle decade from the ticket projection.

    Decade is classified from TicketOpened.vehicle (year + make + model).
    Revenue is the sum of closed ticket line-item totals (integer cents).
    dwell_p50_min is Hyndman-Fan type 7 over closed-ticket dwell minutes.
    Still-open tickets count toward tickets/open but not revenue or dwell.
    Sorted by revenue_cents desc, then newest decade first.
    """
    tickets = project(events)
    by: dict[str, list[Ticket]] = {}
    for ticket in tickets.values():
        by.setdefault(decade_of(ticket.vehicle), []).append(ticket)

    rows: list[DecadeRow] = []
    for decade, group in by.items():
        closed = [t for t in group if t.closed]
        dwells = [
            (t.closed_at - t.opened_at).total_seconds() / 60.0
            for t in closed
            if t.closed_at is not None
        ]
        rows.append(
            DecadeRow(
                decade=decade,
                tickets=len(group),
                closed=len(closed),
                open=len(group) - len(closed),
                revenue_cents=sum(t.total_cents for t in closed),
                dwell_p50_min=percentile(dwells, 0.50),
            )
        )
    rows.sort(key=lambda row: (-row.revenue_cents, _decade_rank(row.decade), row.decade))
    return rows
