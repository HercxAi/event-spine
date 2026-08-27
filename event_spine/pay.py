"""Payment-method fold of the log. Disposable rows; the payment facts stay put."""

from __future__ import annotations

from dataclasses import dataclass

from event_spine.events import PAYMENT_TYPES, Event, EventType


@dataclass(frozen=True, slots=True)
class PayRow:
    method: str
    captured: int
    failed: int
    attempts: int
    captured_cents: int
    failed_cents: int
    fail_rate: float


def by_method(events: list[Event]) -> list[PayRow]:
    """Rebuild one row per payment method from PaymentCaptured / PaymentFailed.

    captured_cents is the sum of successful amount_cents (same as hours
    revenue). failed_cents is the declined/timeout/network attempts — not
    revenue. fail_rate is failed / attempts, or 0.0 when a method has none.
    Sorted by captured_cents desc, then method.
    """
    captured: dict[str, int] = {}
    failed: dict[str, int] = {}
    cap_cents: dict[str, int] = {}
    fail_cents: dict[str, int] = {}

    for event in events:
        if event.type not in PAYMENT_TYPES:
            continue
        method = str(event.payload.get("method") or "")
        amount = int(event.payload.get("amount_cents", 0))
        captured.setdefault(method, 0)
        failed.setdefault(method, 0)
        cap_cents.setdefault(method, 0)
        fail_cents.setdefault(method, 0)
        if event.type is EventType.PAYMENT_CAPTURED:
            captured[method] += 1
            cap_cents[method] += amount
        else:
            failed[method] += 1
            fail_cents[method] += amount

    rows: list[PayRow] = []
    for method in captured:
        n_cap = captured[method]
        n_fail = failed[method]
        attempts = n_cap + n_fail
        rows.append(
            PayRow(
                method=method,
                captured=n_cap,
                failed=n_fail,
                attempts=attempts,
                captured_cents=cap_cents[method],
                failed_cents=fail_cents[method],
                fail_rate=(n_fail / attempts) if attempts else 0.0,
            )
        )
    rows.sort(key=lambda row: (-row.captured_cents, row.method))
    return rows
