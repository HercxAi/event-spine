"""Line-count fold of the log. Disposable rows; LineItemAdded stays put."""

from __future__ import annotations

from dataclasses import dataclass

from event_spine.events import Event
from event_spine.project import project
from event_spine.stats import percentile

# Half-open bands on closed-ticket line count: [lo, hi). Last band is open-ended.
BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("1", 1, 2),
    ("2", 2, 3),
    ("3", 3, 4),
    ("4+", 4, None),
)


@dataclass(frozen=True, slots=True)
class LinesRow:
    bucket: str
    tickets: int
    revenue_cents: int
    total_p50_cents: int | None


def _bucket_for(count: int) -> str:
    for label, lo, hi in BUCKETS:
        if count < lo:
            continue
        if hi is None or count < hi:
            return label
    return BUCKETS[-1][0]


def by_lines(events: list[Event]) -> list[LinesRow]:
    """Rebuild one row per closed-ticket line-item count band.

    Count is len(items) on the ticket projection. Revenue is the closed
    ticket's line-item sum (integer cents). Still-open tickets are ignored
    — they have no close fact yet. Every band is emitted in fixed order,
    including empty ones, so the histogram shape is stable.
    total_p50_cents is Hyndman-Fan type 7 inside the band (rounded to the
    nearest cent), or None when empty.
    """
    tickets = project(events)
    totals: dict[str, list[float]] = {label: [] for label, _, _ in BUCKETS}
    revenue: dict[str, int] = {label: 0 for label, _, _ in BUCKETS}

    for ticket in tickets.values():
        if not ticket.closed:
            continue
        label = _bucket_for(len(ticket.items))
        cents = ticket.total_cents
        totals[label].append(float(cents))
        revenue[label] += cents

    rows: list[LinesRow] = []
    for label, _, _ in BUCKETS:
        group = totals[label]
        p50 = percentile(group, 0.50)
        rows.append(
            LinesRow(
                bucket=label,
                tickets=len(group),
                revenue_cents=revenue[label],
                total_p50_cents=None if p50 is None else int(round(p50)),
            )
        )
    return rows
