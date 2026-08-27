"""SKU fold of the log. Disposable rows; the LineItemAdded facts stay put."""

from __future__ import annotations

from dataclasses import dataclass

from event_spine.events import Event, EventType


@dataclass(frozen=True, slots=True)
class SkuRow:
    sku: str
    description: str
    lines: int
    qty: int
    ext_cents: int


def by_sku(events: list[Event]) -> list[SkuRow]:
    """Rebuild one row per SKU from LineItemAdded. Sorted by ext_cents desc, then sku.

    qty is the sum of payload qty. ext_cents is qty * unit_cents per line,
    summed — the same math as Ticket.total_cents, just grouped by catalog code.
    Description is the last non-empty description seen for that SKU (stable
    enough for a one-day demo; the events still hold every wording).
    """
    lines: dict[str, int] = {}
    qty: dict[str, int] = {}
    ext: dict[str, int] = {}
    desc: dict[str, str] = {}

    for event in events:
        if event.type is not EventType.LINE_ITEM_ADDED:
            continue
        sku = str(event.payload["sku"])
        n = int(event.payload.get("qty", 1))
        unit = int(event.payload["unit_cents"])
        lines[sku] = lines.get(sku, 0) + 1
        qty[sku] = qty.get(sku, 0) + n
        ext[sku] = ext.get(sku, 0) + n * unit
        wording = str(event.payload.get("description") or "").strip()
        if wording:
            desc[sku] = wording
        elif sku not in desc:
            desc[sku] = ""

    rows = [
        SkuRow(
            sku=sku,
            description=desc.get(sku, ""),
            lines=lines[sku],
            qty=qty[sku],
            ext_cents=ext[sku],
        )
        for sku in lines
    ]
    rows.sort(key=lambda row: (-row.ext_cents, row.sku))
    return rows
