"""Family fold of the log. Disposable rows; the LineItemAdded facts stay put."""

from __future__ import annotations

from dataclasses import dataclass

from event_spine.events import Event, EventType


@dataclass(frozen=True, slots=True)
class FamilyRow:
    family: str
    skus: int
    lines: int
    qty: int
    ext_cents: int


def family_of(sku: str) -> str:
    """SKU prefix before the first hyphen. Bare codes stay whole; blank or leading-hyphen → '?'."""
    if not sku or sku.startswith("-"):
        return "?"
    return sku.split("-", 1)[0]


def by_family(events: list[Event]) -> list[FamilyRow]:
    """Rebuild one row per catalog family from LineItemAdded.

    Family is the SKU prefix before the first hyphen (OIL-CONV → OIL).
    Bare codes stay whole (INSP → INSP). Blank or leading-hyphen codes
    become '?'. qty is the sum of payload qty. ext_cents is qty *
    unit_cents per line, summed — the same math as Ticket.total_cents,
    grouped by family. skus is the distinct catalog codes in that
    family. Sorted by ext_cents desc, then family.
    """
    lines: dict[str, int] = {}
    qty: dict[str, int] = {}
    ext: dict[str, int] = {}
    codes: dict[str, set[str]] = {}

    for event in events:
        if event.type is not EventType.LINE_ITEM_ADDED:
            continue
        sku = str(event.payload["sku"])
        family = family_of(sku)
        n = int(event.payload.get("qty", 1))
        unit = int(event.payload["unit_cents"])
        lines[family] = lines.get(family, 0) + 1
        qty[family] = qty.get(family, 0) + n
        ext[family] = ext.get(family, 0) + n * unit
        codes.setdefault(family, set()).add(sku)

    rows = [
        FamilyRow(
            family=family,
            skus=len(codes[family]),
            lines=lines[family],
            qty=qty[family],
            ext_cents=ext[family],
        )
        for family in lines
    ]
    rows.sort(key=lambda row: (-row.ext_cents, row.family))
    return rows
