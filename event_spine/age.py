"""Age fold of the log. Disposable rows; TicketOpened still owns the vehicle."""

from __future__ import annotations

from dataclasses import dataclass

from event_spine.events import Event
from event_spine.project import Ticket, project
from event_spine.simulate import DAY
from event_spine.stats import percentile
from event_spine.year import year_of

BANDS = ("0-4", "5-9", "10-14", "15-19", "20+")


@dataclass(frozen=True, slots=True)
class AgeRow:
    age: str
    tickets: int
    closed: int
    open: int
    revenue_cents: int
    dwell_p50_min: float | None


def age_of(vehicle: str, as_of_year: int) -> str:
    """Bucket a '2018 Honda Civic' plate into years-old as of as_of_year.

    Age is as_of_year minus the leading four-digit model year. Bands are
    0-4, 5-9, 10-14, 15-19, 20+. A future model year lands in 0-4. Missing
    or unparseable years stay empty so the renderer can print —.
    """
    year = year_of(vehicle)
    if not year:
        return ""
    years_old = as_of_year - int(year)
    if years_old <= 4:
        return "0-4"
    if years_old <= 9:
        return "5-9"
    if years_old <= 14:
        return "10-14"
    if years_old <= 19:
        return "15-19"
    return "20+"


def _band_rank(age: str) -> int:
    try:
        return BANDS.index(age)
    except ValueError:
        return len(BANDS)


def by_age(events: list[Event]) -> list[AgeRow]:
    """Rebuild one row per vehicle age band from the ticket projection.

    Age is classified from TicketOpened.vehicle against the shop day's
    year. Revenue is the sum of closed ticket line-item totals (integer
    cents). dwell_p50_min is Hyndman-Fan type 7 over closed-ticket dwell
    minutes. Still-open tickets count toward tickets/open but not revenue
    or dwell. Sorted by revenue_cents desc, then newest band first.
    """
    as_of_year = events[0].occurred_at.year if events else DAY.year
    tickets = project(events)
    by: dict[str, list[Ticket]] = {}
    for ticket in tickets.values():
        by.setdefault(age_of(ticket.vehicle, as_of_year), []).append(ticket)

    rows: list[AgeRow] = []
    for age, group in by.items():
        closed = [t for t in group if t.closed]
        dwells = [
            (t.closed_at - t.opened_at).total_seconds() / 60.0
            for t in closed
            if t.closed_at is not None
        ]
        rows.append(
            AgeRow(
                age=age,
                tickets=len(group),
                closed=len(closed),
                open=len(group) - len(closed),
                revenue_cents=sum(t.total_cents for t in closed),
                dwell_p50_min=percentile(dwells, 0.50),
            )
        )
    rows.sort(key=lambda row: (-row.revenue_cents, _band_rank(row.age), row.age))
    return rows
