"""Grade fold of the log. Disposable rows; LineItemAdded still owns the oil SKU."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from event_spine.events import Event
from event_spine.project import LineItem, Ticket, project
from event_spine.stats import percentile


# Highest rank wins when a ticket carries more than one oil SKU.
_GRADE_RANK = {
    "conventional": 1,
    "synthetic": 2,
    "full-synth": 3,
}


@dataclass(frozen=True, slots=True)
class GradeRow:
    grade: str
    tickets: int
    closed: int
    open: int
    revenue_cents: int
    dwell_p50_min: float | None


def _grade_from_sku(sku: str) -> str:
    if sku.startswith("OIL-FS"):
        return "full-synth"
    if sku.startswith("OIL-SYN"):
        return "synthetic"
    if sku.startswith("OIL-CONV"):
        return "conventional"
    return ""


def _skus_of(source: object) -> list[str]:
    """Pull SKU strings from a ticket, line items, or a list of codes."""
    if source is None:
        return []
    if isinstance(source, str):
        return [source]
    if isinstance(source, Ticket):
        return [item.sku for item in source.items]
    if isinstance(source, LineItem):
        return [source.sku]
    if isinstance(source, Iterable):
        out: list[str] = []
        for item in source:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, LineItem):
                out.append(item.sku)
            else:
                sku = getattr(item, "sku", None)
                if sku is not None:
                    out.append(str(sku))
                else:
                    out.append(str(item))
        return out
    sku = getattr(source, "sku", None)
    if sku is not None:
        return [str(sku)]
    return []


def grade_of(source: object) -> str:
    """Map oil SKUs on a ticket to conventional, synthetic, or full-synth.

    Classifies from LineItem.sku prefixes (OIL-CONV / OIL-SYN / OIL-FS), a
    ticket, or a list of sku strings. Highest grade wins when more than one
    oil SKU is present. Empty items or no oil SKU stay empty so the
    renderer can print —.
    """
    best = ""
    best_rank = 0
    for sku in _skus_of(source):
        grade = _grade_from_sku(sku)
        rank = _GRADE_RANK.get(grade, 0)
        if rank > best_rank:
            best = grade
            best_rank = rank
    return best


def by_grade(events: list[Event]) -> list[GradeRow]:
    """Rebuild one row per oil grade from the ticket projection.

    Grade is classified from LineItemAdded oil SKUs on each ticket.
    Revenue is the sum of closed ticket line-item totals (integer cents).
    dwell_p50_min is Hyndman-Fan type 7 over closed-ticket dwell minutes.
    Still-open tickets count toward tickets/open but not revenue or dwell.
    Sorted by revenue_cents desc, then grade.
    """
    tickets = project(events)
    by: dict[str, list[Ticket]] = {}
    for ticket in tickets.values():
        by.setdefault(grade_of(ticket.items), []).append(ticket)

    rows: list[GradeRow] = []
    for grade, group in by.items():
        closed = [t for t in group if t.closed]
        dwells = [
            (t.closed_at - t.opened_at).total_seconds() / 60.0
            for t in closed
            if t.closed_at is not None
        ]
        rows.append(
            GradeRow(
                grade=grade,
                tickets=len(group),
                closed=len(closed),
                open=len(group) - len(closed),
                revenue_cents=sum(t.total_cents for t in closed),
                dwell_p50_min=percentile(dwells, 0.50),
            )
        )
    rows.sort(key=lambda row: (-row.revenue_cents, row.grade))
    return rows
