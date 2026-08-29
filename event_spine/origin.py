"""Origin fold of the log. Disposable rows; TicketOpened still owns the vehicle."""

from __future__ import annotations

from dataclasses import dataclass

from event_spine.events import Event
from event_spine.make import make_of
from event_spine.project import Ticket, project
from event_spine.stats import percentile


JAPAN = frozenset(
    {
        "Honda",
        "Toyota",
        "Subaru",
        "Mazda",
        "Nissan",
        "Mitsubishi",
        "Suzuki",
        "Lexus",
        "Infiniti",
        "Acura",
    }
)

US = frozenset(
    {
        "Ford",
        "Chevy",
        "Chevrolet",
        "Jeep",
        "Ram",
        "GMC",
        "Cadillac",
        "Lincoln",
        "Buick",
        "Dodge",
        "Tesla",
        "Chrysler",
    }
)

KOREA = frozenset({"Hyundai", "Kia", "Genesis"})

GERMANY = frozenset(
    {
        "BMW",
        "Mercedes",
        "Audi",
        "Volkswagen",
        "Porsche",
        "Mini",
    }
)


@dataclass(frozen=True, slots=True)
class OriginRow:
    origin: str
    tickets: int
    closed: int
    open: int
    revenue_cents: int
    dwell_p50_min: float | None


def origin_of(vehicle: str) -> str:
    """Map a '2018 Honda Civic' style plate to Japan, US, Korea, or Germany.

    Uses the parsed make token. Known Japanese, American, Korean, and German
    manufacturers map to those origins. Empty, unparseable, or unknown makes
    stay empty so the renderer can print —.
    """
    make = make_of(vehicle)
    if not make:
        return ""
    if make in JAPAN:
        return "Japan"
    if make in US:
        return "US"
    if make in KOREA:
        return "Korea"
    if make in GERMANY:
        return "Germany"
    return ""


def by_origin(events: list[Event]) -> list[OriginRow]:
    """Rebuild one row per vehicle origin from the ticket projection.

    Origin is classified from TicketOpened.vehicle (year + make + model).
    Revenue is the sum of closed ticket line-item totals (integer cents).
    dwell_p50_min is Hyndman-Fan type 7 over closed-ticket dwell minutes.
    Still-open tickets count toward tickets/open but not revenue or dwell.
    Sorted by revenue_cents desc, then origin.
    """
    tickets = project(events)
    by: dict[str, list[Ticket]] = {}
    for ticket in tickets.values():
        by.setdefault(origin_of(ticket.vehicle), []).append(ticket)

    rows: list[OriginRow] = []
    for origin, group in by.items():
        closed = [t for t in group if t.closed]
        dwells = [
            (t.closed_at - t.opened_at).total_seconds() / 60.0
            for t in closed
            if t.closed_at is not None
        ]
        rows.append(
            OriginRow(
                origin=origin,
                tickets=len(group),
                closed=len(closed),
                open=len(group) - len(closed),
                revenue_cents=sum(t.total_cents for t in closed),
                dwell_p50_min=percentile(dwells, 0.50),
            )
        )
    rows.sort(key=lambda row: (-row.revenue_cents, row.origin))
    return rows
