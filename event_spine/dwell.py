"""Dwell-bucket fold of the log. Disposable rows; open/close facts stay put."""

from __future__ import annotations

from dataclasses import dataclass

from event_spine.events import Event
from event_spine.project import project
from event_spine.stats import percentile

# Half-open bands in minutes: [lo, hi). The last band is open-ended.
BUCKETS: tuple[tuple[str, float, float | None], ...] = (
    ("<5", 0.0, 5.0),
    ("5-15", 5.0, 15.0),
    ("15-60", 15.0, 60.0),
    ("60+", 60.0, None),
)


@dataclass(frozen=True, slots=True)
class DwellRow:
    bucket: str
    tickets: int
    revenue_cents: int
    dwell_p50_min: float | None


def _bucket_for(minutes: float) -> str:
    for label, lo, hi in BUCKETS:
        if minutes < lo:
            continue
        if hi is None or minutes < hi:
            return label
    return BUCKETS[-1][0]


def by_dwell(events: list[Event]) -> list[DwellRow]:
    """Rebuild one row per dwell band from closed tickets.

    Dwell is (TicketClosed − TicketOpened) in minutes. Revenue is the
    closed ticket's line-item total (integer cents). Still-open tickets
    are ignored — they have no close fact yet. Every band is emitted in
    fixed order, including empty ones, so the histogram shape is stable.
    dwell_p50_min is Hyndman-Fan type 7 inside the band, or None when empty.
    """
    tickets = project(events)
    dwells: dict[str, list[float]] = {label: [] for label, _, _ in BUCKETS}
    revenue: dict[str, int] = {label: 0 for label, _, _ in BUCKETS}

    for ticket in tickets.values():
        if not ticket.closed or ticket.closed_at is None:
            continue
        minutes = (ticket.closed_at - ticket.opened_at).total_seconds() / 60.0
        label = _bucket_for(minutes)
        dwells[label].append(minutes)
        revenue[label] += ticket.total_cents

    rows: list[DwellRow] = []
    for label, _, _ in BUCKETS:
        group = dwells[label]
        rows.append(
            DwellRow(
                bucket=label,
                tickets=len(group),
                revenue_cents=revenue[label],
                dwell_p50_min=percentile(group, 0.50) if group else None,
            )
        )
    return rows
