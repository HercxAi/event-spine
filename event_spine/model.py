"""Model fold of the log. Disposable rows; TicketOpened still owns the vehicle."""

from __future__ import annotations

from dataclasses import dataclass

from event_spine.events import Event
from event_spine.project import Ticket, project
from event_spine.stats import percentile


@dataclass(frozen=True, slots=True)
class ModelRow:
    model: str
    tickets: int
    closed: int
    open: int
    revenue_cents: int
    dwell_p50_min: float | None


def model_of(vehicle: str) -> str:
    """Pull the model tokens off a '2018 Honda Civic' style plate.

    A leading four-digit year is skipped, then the make token, and the
    remainder joins as the model. Bare single-token names stay empty so
    the renderer can print —. Empty or whitespace-only strings stay empty.
    """
    parts = vehicle.strip().split()
    if not parts:
        return ""
    if len(parts[0]) == 4 and parts[0].isdigit():
        parts = parts[1:]
    if len(parts) < 2:
        return ""
    return " ".join(parts[1:])


def by_model(events: list[Event]) -> list[ModelRow]:
    """Rebuild one row per vehicle model from the ticket projection.

    Model is parsed from TicketOpened.vehicle (year + make + model). Revenue
    is the sum of closed ticket line-item totals (integer cents).
    dwell_p50_min is Hyndman-Fan type 7 over closed-ticket dwell minutes.
    Still-open tickets count toward tickets/open but not revenue or dwell.
    Sorted by revenue_cents desc, then model.
    """
    tickets = project(events)
    by: dict[str, list[Ticket]] = {}
    for ticket in tickets.values():
        by.setdefault(model_of(ticket.vehicle), []).append(ticket)

    rows: list[ModelRow] = []
    for model, group in by.items():
        closed = [t for t in group if t.closed]
        dwells = [
            (t.closed_at - t.opened_at).total_seconds() / 60.0
            for t in closed
            if t.closed_at is not None
        ]
        rows.append(
            ModelRow(
                model=model,
                tickets=len(group),
                closed=len(closed),
                open=len(group) - len(closed),
                revenue_cents=sum(t.total_cents for t in closed),
                dwell_p50_min=percentile(dwells, 0.50),
            )
        )
    rows.sort(key=lambda row: (-row.revenue_cents, row.model))
    return rows
