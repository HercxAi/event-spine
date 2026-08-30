"""Viscosity fold of the log. Disposable rows; LineItemAdded still owns the oil."""

from __future__ import annotations

import re
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

_SAE = re.compile(r"\b(\d{1,2}W-\d{2})\b", re.IGNORECASE)

# Menu oils whose descriptions already carry SAE; SKU fallback when they do not.
_SKU_VISCOSITY = (
    ("OIL-FS", "0W-20"),
    ("OIL-SYN", "5W-30"),
    ("OIL-CONV", "5W-30"),
)


@dataclass(frozen=True, slots=True)
class ViscosityRow:
    viscosity: str
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


def _viscosity_from_sku(sku: str) -> str:
    for prefix, weight in _SKU_VISCOSITY:
        if sku.startswith(prefix):
            return weight
    return ""


def _viscosity_from_item(sku: str, description: str) -> str:
    match = _SAE.search(description)
    if match:
        return match.group(1).upper()
    return _viscosity_from_sku(sku)


def _items_of(source: object) -> list[tuple[str, str]]:
    """Pull (sku, description) from a ticket, line items, or a list of codes."""
    if source is None:
        return []
    if isinstance(source, str):
        return [(source, "")]
    if isinstance(source, Ticket):
        return [(item.sku, item.description) for item in source.items]
    if isinstance(source, LineItem):
        return [(source.sku, source.description)]
    if isinstance(source, Iterable):
        out: list[tuple[str, str]] = []
        for item in source:
            if isinstance(item, str):
                out.append((item, ""))
            elif isinstance(item, LineItem):
                out.append((item.sku, item.description))
            else:
                sku = getattr(item, "sku", None)
                if sku is not None:
                    desc = getattr(item, "description", "") or ""
                    out.append((str(sku), str(desc)))
                else:
                    out.append((str(item), ""))
        return out
    sku = getattr(source, "sku", None)
    if sku is not None:
        desc = getattr(source, "description", "") or ""
        return [(str(sku), str(desc))]
    return []


def viscosity_of(source: object) -> str:
    """Map oil line items on a ticket to an SAE viscosity (e.g. 5W-30, 0W-20).

    Parses SAE from LineItem.description with a word-boundary regex; falls
    back from SKU prefixes (OIL-CONV / OIL-SYN → 5W-30, OIL-FS → 0W-20)
    when the description has no weight. Highest oil grade wins when more
    than one oil item is present. Empty items or no oil stay empty so the
    renderer can print —.
    """
    best = ""
    best_rank = 0
    for sku, description in _items_of(source):
        grade = _grade_from_sku(sku)
        rank = _GRADE_RANK.get(grade, 0)
        if rank > best_rank:
            weight = _viscosity_from_item(sku, description)
            if weight:
                best = weight
                best_rank = rank
    return best


def by_viscosity(events: list[Event]) -> list[ViscosityRow]:
    """Rebuild one row per SAE viscosity from the ticket projection.

    Viscosity is classified from LineItemAdded oil line items on each
    ticket. Revenue is the sum of closed ticket line-item totals (integer
    cents). dwell_p50_min is Hyndman-Fan type 7 over closed-ticket dwell
    minutes. Still-open tickets count toward tickets/open but not revenue
    or dwell. Sorted by revenue_cents desc, then viscosity.
    """
    tickets = project(events)
    by: dict[str, list[Ticket]] = {}
    for ticket in tickets.values():
        by.setdefault(viscosity_of(ticket.items), []).append(ticket)

    rows: list[ViscosityRow] = []
    for viscosity, group in by.items():
        closed = [t for t in group if t.closed]
        dwells = [
            (t.closed_at - t.opened_at).total_seconds() / 60.0
            for t in closed
            if t.closed_at is not None
        ]
        rows.append(
            ViscosityRow(
                viscosity=viscosity,
                tickets=len(group),
                closed=len(closed),
                open=len(group) - len(closed),
                revenue_cents=sum(t.total_cents for t in closed),
                dwell_p50_min=percentile(dwells, 0.50),
            )
        )
    rows.sort(key=lambda row: (-row.revenue_cents, row.viscosity))
    return rows
