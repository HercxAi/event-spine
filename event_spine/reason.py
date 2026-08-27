"""Payment-failure reason fold. Disposable rows; PaymentFailed facts stay put."""

from __future__ import annotations

from dataclasses import dataclass

from event_spine.events import Event, EventType


@dataclass(frozen=True, slots=True)
class ReasonRow:
    reason: str
    fails: int
    ask_cents: int
    methods: tuple[str, ...]


def by_reason(events: list[Event]) -> list[ReasonRow]:
    """Rebuild one row per PaymentFailed reason.

    ask_cents is the sum of amount_cents on those fails (the declined ask,
    not revenue). methods lists distinct tender strings seen for that
    reason, sorted. Sorted by fails desc, then reason.
    """
    fails: dict[str, int] = {}
    ask: dict[str, int] = {}
    methods: dict[str, set[str]] = {}

    for event in events:
        if event.type is not EventType.PAYMENT_FAILED:
            continue
        reason = str(event.payload.get("reason") or "").strip() or "?"
        method = str(event.payload.get("method") or "").strip() or "?"
        amount = int(event.payload.get("amount_cents", 0))
        fails[reason] = fails.get(reason, 0) + 1
        ask[reason] = ask.get(reason, 0) + amount
        methods.setdefault(reason, set()).add(method)

    rows = [
        ReasonRow(
            reason=reason,
            fails=fails[reason],
            ask_cents=ask[reason],
            methods=tuple(sorted(methods[reason])),
        )
        for reason in fails
    ]
    rows.sort(key=lambda row: (-row.fails, row.reason))
    return rows
