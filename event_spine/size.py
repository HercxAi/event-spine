"""Ticket-total band fold of the log. Disposable rows; closed totals stay put."""

from __future__ import annotations

from dataclasses import dataclass

from event_spine.events import Event
from event_spine.project import project
from event_spine.stats import percentile

# Half-open bands in integer cents: [lo, hi). The last band is open-ended.
BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("<$50", 0, 5000),
    ("$50-100", 5000, 10000),
    ("$100-200", 10000, 20000),
    ("$200+", 20000, None),
)


@dataclass(frozen=True, slots=True)
class SizeRow:
    bucket: str
    tickets: int
    revenue_cents: int
    total_p50_cents: int | None


def _bucket_for(cents: int) -> str:
    for label, lo, hi in BUCKETS:
        if cents < lo:
            continue
        if hi is None or cents < hi:
            return label
    return BUCKETS[-1][0]


def by_size(events: list[Event]) -> list[SizeRow]:
    """Rebuild one row per closed-ticket total band.

    Total is the closed ticket's line-item sum (integer cents). Revenue is
    the same sum, aggregated per band. Still-open tickets are ignored —
    they have no close fact yet. Every band is emitted in fixed order,
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
        cents = ticket.total_cents
        label = _bucket_for(cents)
        totals[label].append(float(cents))
        revenue[label] += cents

    rows: list[SizeRow] = []
    for label, _, _ in BUCKETS:
        group = totals[label]
        p50 = percentile(group, 0.50)
        rows.append(
            SizeRow(
                bucket=label,
                tickets=len(group),
                revenue_cents=revenue[label],
                total_p50_cents=None if p50 is None else int(round(p50)),
            )
        )
    return rows
