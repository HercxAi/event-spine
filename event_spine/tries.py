"""Payment-attempt fold of the log. Disposable rows; payment facts stay put."""

from __future__ import annotations

from dataclasses import dataclass

from event_spine.events import Event
from event_spine.project import project
from event_spine.stats import percentile

# Half-open bands on closed-ticket payment count: [lo, hi). Last band is open-ended.
BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("1", 1, 2),
    ("2", 2, 3),
    ("3+", 3, None),
)


@dataclass(frozen=True, slots=True)
class TriesRow:
    bucket: str
    tickets: int
    revenue_cents: int
    total_p50_cents: int | None


def _bucket_for(count: int) -> str:
    if count < 1:
        # Closed with zero payments still needs a band; treat as first-try shape.
        return BUCKETS[0][0]
    for label, lo, hi in BUCKETS:
        if count < lo:
            continue
        if hi is None or count < hi:
            return label
    return BUCKETS[-1][0]


def by_tries(events: list[Event]) -> list[TriesRow]:
    """Rebuild one row per closed-ticket payment-attempt band.

    Count is len(payments) on the ticket projection (captures + fails).
    Revenue is the closed ticket's line-item sum (integer cents). Still-open
    tickets are ignored — they have no close fact yet. Every band is emitted
    in fixed order, including empty ones, so the histogram shape is stable.
    total_p50_cents is Hyndman-Fan type 7 inside the band (rounded to the
    nearest cent), or None when empty.
    """
    tickets = project(events)
    totals: dict[str, list[float]] = {label: [] for label, _, _ in BUCKETS}
    revenue: dict[str, int] = {label: 0 for label, _, _ in BUCKETS}

    for ticket in tickets.values():
        if not ticket.closed:
            continue
        label = _bucket_for(len(ticket.payments))
        cents = ticket.total_cents
        totals[label].append(float(cents))
        revenue[label] += cents

    rows: list[TriesRow] = []
    for label, _, _ in BUCKETS:
        group = totals[label]
        p50 = percentile(group, 0.50)
        rows.append(
            TriesRow(
                bucket=label,
                tickets=len(group),
                revenue_cents=revenue[label],
                total_p50_cents=None if p50 is None else int(round(p50)),
            )
        )
    return rows
