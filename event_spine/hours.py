"""Hourly fold of the log. Disposable bins; the events stay put."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from event_spine.events import Event, EventType
from event_spine.simulate import DAY, SimConfig


@dataclass(frozen=True, slots=True)
class HourBin:
    hour: datetime
    tickets_opened: int
    payments_captured: int
    payments_failed: int
    revenue_cents: int
    peak_open: int


def _hour_start(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def by_hour(
    events: list[Event],
    *,
    open_hour: int = SimConfig.open_hour,
    close_hour: int = SimConfig.close_hour,
) -> list[HourBin]:
    """Rebuild one bin per shop-open hour, plus any hour that actually has events.

    Empty shop hours stay in the table (zeros). A quiet hour still reports
    peak concurrent tickets carried in from earlier opens. Revenue is the
    sum of PaymentCaptured.amount_cents in that hour, integer cents.
    """
    if events:
        tz = events[0].occurred_at.tzinfo or UTC
        dates = sorted({event.occurred_at.date() for event in events})
    else:
        tz = UTC
        dates = [DAY]

    hours: list[datetime] = []
    seen: set[datetime] = set()
    for day in dates:
        for h in range(open_hour, close_hour):
            start = datetime(day.year, day.month, day.day, h, tzinfo=tz)
            if start not in seen:
                hours.append(start)
                seen.add(start)
    for event in events:
        start = _hour_start(event.occurred_at)
        if start not in seen:
            hours.append(start)
            seen.add(start)
    hours.sort()

    opened = {h: 0 for h in hours}
    captured = {h: 0 for h in hours}
    failed = {h: 0 for h in hours}
    revenue = {h: 0 for h in hours}
    peak = {h: 0 for h in hours}

    ordered = sorted(events, key=lambda e: (e.occurred_at, e.ticket_id, e.type.value))
    concurrent = 0
    idx = 0
    n = len(ordered)
    for hour in hours:
        end = hour + timedelta(hours=1)
        hour_peak = concurrent
        while idx < n and ordered[idx].occurred_at < end:
            event = ordered[idx]
            if event.type is EventType.TICKET_OPENED:
                opened[hour] += 1
                concurrent += 1
            elif event.type is EventType.TICKET_CLOSED:
                concurrent = max(0, concurrent - 1)
            elif event.type is EventType.PAYMENT_CAPTURED:
                captured[hour] += 1
                revenue[hour] += int(event.payload.get("amount_cents", 0))
            elif event.type is EventType.PAYMENT_FAILED:
                failed[hour] += 1
            hour_peak = max(hour_peak, concurrent)
            idx += 1
        peak[hour] = hour_peak

    return [
        HourBin(
            hour=hour,
            tickets_opened=opened[hour],
            payments_captured=captured[hour],
            payments_failed=failed[hour],
            revenue_cents=revenue[hour],
            peak_open=peak[hour],
        )
        for hour in hours
    ]
