"""Segment fold of the log. Disposable rows; TicketOpened still owns the vehicle."""

from __future__ import annotations

from dataclasses import dataclass

from event_spine.body import SUV_MODELS, TRUCK_MODELS
from event_spine.events import Event
from event_spine.make import make_of
from event_spine.model import model_of
from event_spine.project import Ticket, project
from event_spine.stats import percentile


LUXURY_MAKES = frozenset(
    {
        "BMW",
        "Mercedes",
        "Mercedes-Benz",
        "Audi",
        "Lexus",
        "Acura",
        "Infiniti",
        "Cadillac",
        "Lincoln",
        "Volvo",
        "Genesis",
        "Porsche",
        "Jaguar",
        "Tesla",
    }
)

# Pickup names that are not already in the body-fold truck set.
TRUCK = TRUCK_MODELS | frozenset({"Gladiator", "Cybertruck"})

# Crossovers / SUVs beyond the body-fold set. Luxury-make SUVs still
# classify as luxury unless the model is a pickup (truck wins).
SUV = SUV_MODELS | frozenset(
    {
        "Equinox",
        "Tahoe",
        "Suburban",
        "Expedition",
        "Pathfinder",
        "Rogue",
        "Santa Fe",
        "Palisade",
        "Telluride",
        "Durango",
    }
)


@dataclass(frozen=True, slots=True)
class SegmentRow:
    segment: str
    tickets: int
    closed: int
    open: int
    revenue_cents: int
    dwell_p50_min: float | None


def segment_of(vehicle: str) -> str:
    """Map a '2018 Honda Civic' style plate to luxury, truck, suv, or car.

    Pickup / truck model names win even on a luxury badge. Known luxury
    makes map to luxury. Known crossovers and SUVs map to suv. Any other
    named model is car. Empty or unparseable plates stay empty so the
    renderer can print —.
    """
    model = model_of(vehicle)
    if not model:
        return ""
    if model in TRUCK:
        return "truck"
    make = make_of(vehicle)
    if make in LUXURY_MAKES or (make == "Land" and model.startswith("Rover")):
        return "luxury"
    if model in SUV:
        return "suv"
    return "car"


def by_segment(events: list[Event]) -> list[SegmentRow]:
    """Rebuild one row per vehicle market segment from the ticket projection.

    Segment is classified from TicketOpened.vehicle (year + make + model).
    Revenue is the sum of closed ticket line-item totals (integer cents).
    dwell_p50_min is Hyndman-Fan type 7 over closed-ticket dwell minutes.
    Still-open tickets count toward tickets/open but not revenue or dwell.
    Sorted by revenue_cents desc, then segment.
    """
    tickets = project(events)
    by: dict[str, list[Ticket]] = {}
    for ticket in tickets.values():
        by.setdefault(segment_of(ticket.vehicle), []).append(ticket)

    rows: list[SegmentRow] = []
    for segment, group in by.items():
        closed = [t for t in group if t.closed]
        dwells = [
            (t.closed_at - t.opened_at).total_seconds() / 60.0
            for t in closed
            if t.closed_at is not None
        ]
        rows.append(
            SegmentRow(
                segment=segment,
                tickets=len(group),
                closed=len(closed),
                open=len(group) - len(closed),
                revenue_cents=sum(t.total_cents for t in closed),
                dwell_p50_min=percentile(dwells, 0.50),
            )
        )
    rows.sort(key=lambda row: (-row.revenue_cents, row.segment))
    return rows
