"""Body fold of the log. Disposable rows; TicketOpened still owns the vehicle."""

from __future__ import annotations

from dataclasses import dataclass

from event_spine.events import Event
from event_spine.model import model_of
from event_spine.project import Ticket, project
from event_spine.stats import percentile


TRUCK_MODELS = frozenset(
    {
        "F-150",
        "F-250",
        "F-350",
        "Silverado",
        "Sierra",
        "Ram",
        "Tundra",
        "Tacoma",
        "Ranger",
        "Colorado",
        "Canyon",
        "Frontier",
        "Titan",
        "Ridgeline",
    }
)

SUV_MODELS = frozenset(
    {
        "RAV4",
        "CR-V",
        "Escape",
        "CX-5",
        "Tucson",
        "Grand Cherokee",
        "Outback",
        "Forester",
        "Crosstrek",
        "Explorer",
        "Pilot",
        "Highlander",
        "4Runner",
        "Wrangler",
        "Cherokee",
        "Bronco",
    }
)


@dataclass(frozen=True, slots=True)
class BodyRow:
    body: str
    tickets: int
    closed: int
    open: int
    revenue_cents: int
    dwell_p50_min: float | None


def body_of(vehicle: str) -> str:
    """Map a '2018 Honda Civic' style plate to car, SUV, or truck.

    Uses the parsed model token. Known pickups are truck, known crossovers
    and SUVs are SUV, any other named model is car. Empty or unparseable
    plates stay empty so the renderer can print —.
    """
    model = model_of(vehicle)
    if not model:
        return ""
    if model in TRUCK_MODELS:
        return "truck"
    if model in SUV_MODELS:
        return "SUV"
    return "car"


def by_body(events: list[Event]) -> list[BodyRow]:
    """Rebuild one row per vehicle body from the ticket projection.

    Body is classified from TicketOpened.vehicle (year + make + model).
    Revenue is the sum of closed ticket line-item totals (integer cents).
    dwell_p50_min is Hyndman-Fan type 7 over closed-ticket dwell minutes.
    Still-open tickets count toward tickets/open but not revenue or dwell.
    Sorted by revenue_cents desc, then body.
    """
    tickets = project(events)
    by: dict[str, list[Ticket]] = {}
    for ticket in tickets.values():
        by.setdefault(body_of(ticket.vehicle), []).append(ticket)

    rows: list[BodyRow] = []
    for body, group in by.items():
        closed = [t for t in group if t.closed]
        dwells = [
            (t.closed_at - t.opened_at).total_seconds() / 60.0
            for t in closed
            if t.closed_at is not None
        ]
        rows.append(
            BodyRow(
                body=body,
                tickets=len(group),
                closed=len(closed),
                open=len(group) - len(closed),
                revenue_cents=sum(t.total_cents for t in closed),
                dwell_p50_min=percentile(dwells, 0.50),
            )
        )
    rows.sort(key=lambda row: (-row.revenue_cents, row.body))
    return rows
