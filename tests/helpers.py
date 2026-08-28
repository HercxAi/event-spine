from __future__ import annotations

from datetime import UTC, datetime, timedelta

from event_spine.events import Event, EventType


def at(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 3, 14, hour, minute, second, tzinfo=UTC)


def ev(
    event_id: str,
    typ: EventType,
    when: datetime,
    ticket_id: str,
    **payload: object,
) -> Event:
    return Event(
        event_id=event_id,
        type=typ,
        occurred_at=when,
        ticket_id=ticket_id,
        payload=payload,
    )


def ticket_flow(
    ticket_id: str,
    opened: datetime,
    total_cents: int,
    *,
    prefix: str,
    fail: bool = False,
    items: list[tuple[str, int]] | None = None,
    dwell: timedelta | None = None,
    bay: str = "1",
    vehicle: str = "2018 Honda Civic",
) -> list[Event]:
    """Minimal open → line(s) → pay → close chain."""
    rows = items or [("OIL-CONV", total_cents)]
    t = opened
    seq = 0
    out: list[Event] = []

    def nid() -> str:
        nonlocal seq
        seq += 1
        return f"{prefix}{seq:02d}"

    out.append(ev(nid(), EventType.TICKET_OPENED, t, ticket_id, bay=bay, vehicle=vehicle))
    running = 0
    for sku, cents in rows:
        t = t + timedelta(seconds=30)
        running += cents
        out.append(
            ev(
                nid(),
                EventType.LINE_ITEM_ADDED,
                t,
                ticket_id,
                sku=sku,
                description=sku,
                qty=1,
                unit_cents=cents,
            )
        )
    t = t + timedelta(seconds=20)
    if fail:
        out.append(
            ev(
                nid(),
                EventType.PAYMENT_FAILED,
                t,
                ticket_id,
                method="card",
                amount_cents=running,
                reason="declined",
            )
        )
        t = t + timedelta(seconds=10)
    out.append(
        ev(
            nid(),
            EventType.PAYMENT_CAPTURED,
            t,
            ticket_id,
            method="card",
            amount_cents=running,
        )
    )
    if dwell is not None:
        t = opened + dwell
    else:
        t = t + timedelta(seconds=5)
    out.append(
        ev(nid(), EventType.TICKET_CLOSED, t, ticket_id, total_cents=running)
    )
    return out
