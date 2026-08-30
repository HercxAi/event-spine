"""Family fold of the log. Disposable rows; LineItemAdded still owns the catalog."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from event_spine.events import Event
from event_spine.project import LineItem, Ticket, project
from event_spine.stats import percentile


# Fixed emit order for basket labels (stable strings, readable left-to-right).
_FAMILY_ORDER = ("oil", "filter", "wiper", "service")

# Free multi-point inspection is always on the ticket; skip it in the mix.
_SKIP_SKUS = frozenset({"INSP"})

# Catalog prefixes that are fluid top-offs or heavy shop services (not oil/filter/wiper).
_SERVICE_PREFIXES = ("FLD-", "TRN-", "DIFF-", "BRK-")


@dataclass(frozen=True, slots=True)
class FamilyRow:
    family: str
    tickets: int
    closed: int
    open: int
    revenue_cents: int
    dwell_p50_min: float | None


def _family_from_sku(sku: str) -> str:
    if not sku or sku in _SKIP_SKUS:
        return ""
    if sku.startswith("OIL-"):
        return "oil"
    if sku.startswith("FIL-"):
        return "filter"
    if sku.startswith("WIP-"):
        return "wiper"
    if sku.startswith(_SERVICE_PREFIXES):
        return "service"
    return "service"


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


def family_of(source: object) -> str:
    """Map line-item SKUs on a ticket to a basket mix (e.g. oil+filter).

    Classifies each SKU prefix (OIL- → oil, FIL- → filter, WIP- → wiper,
    FLD-/TRN-/DIFF-/BRK- and other paid catalog codes → service). Free
    INSP is skipped so the mix reflects sold work. Families join in fixed
    order. Empty items stay empty so the renderer can print —.
    """
    present: set[str] = set()
    for sku in _skus_of(source):
        family = _family_from_sku(sku)
        if family:
            present.add(family)
    if not present:
        return ""
    return "+".join(name for name in _FAMILY_ORDER if name in present)


def by_family(events: list[Event]) -> list[FamilyRow]:
    """Rebuild one row per basket mix from the ticket projection.

    Family is classified from LineItemAdded SKU prefixes on each ticket.
    Revenue is the sum of closed ticket line-item totals (integer cents).
    dwell_p50_min is Hyndman-Fan type 7 over closed-ticket dwell minutes.
    Still-open tickets count toward tickets/open but not revenue or dwell.
    Sorted by revenue_cents desc, then family.
    """
    tickets = project(events)
    by: dict[str, list[Ticket]] = {}
    for ticket in tickets.values():
        by.setdefault(family_of(ticket.items), []).append(ticket)

    rows: list[FamilyRow] = []
    for family, group in by.items():
        closed = [t for t in group if t.closed]
        dwells = [
            (t.closed_at - t.opened_at).total_seconds() / 60.0
            for t in closed
            if t.closed_at is not None
        ]
        rows.append(
            FamilyRow(
                family=family,
                tickets=len(group),
                closed=len(closed),
                open=len(group) - len(closed),
                revenue_cents=sum(t.total_cents for t in closed),
                dwell_p50_min=percentile(dwells, 0.50),
            )
        )
    rows.sort(key=lambda row: (-row.revenue_cents, row.family))
    return rows
