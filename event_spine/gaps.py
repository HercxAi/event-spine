"""Shop-hour gaps between TicketOpened. Disposable fold; the events stay put."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from event_spine.events import Event, EventType
from event_spine.hours import SHOP_CLOSE_HOUR, SHOP_OPEN_HOUR


@dataclass(frozen=True, slots=True)
class OpenGap:
    start: datetime
    end: datetime
    minutes: float
    before_event_id: str | None
    after_event_id: str | None


def shop_open_gaps(
    events: list[Event],
    *,
    open_hour: int = SHOP_OPEN_HOUR,
    close_hour: int = SHOP_CLOSE_HOUR,
) -> list[OpenGap]:
    """Rebuild every stretch between TicketOpened during configured shop hours.

    Shop open and shop close bound the day, so a dead first hour or a
    register that dies before close still shows up. Opens outside
    [open, close) do not count. Empty log → no gaps (nothing to rebuild).
    """
    if not events:
        return []

    tz = events[0].occurred_at.tzinfo or UTC
    dates = sorted({event.occurred_at.date() for event in events})
    opens = sorted(
        (e for e in events if e.type is EventType.TICKET_OPENED),
        key=lambda e: (e.occurred_at, e.event_id),
    )

    gaps: list[OpenGap] = []
    for day in dates:
        start = datetime(day.year, day.month, day.day, open_hour, tzinfo=tz)
        end = datetime(day.year, day.month, day.day, close_hour, tzinfo=tz)
        in_hours = [e for e in opens if start <= e.occurred_at < end]
        points: list[tuple[datetime, str | None]] = [(start, None)]
        points.extend((e.occurred_at, e.event_id) for e in in_hours)
        points.append((end, None))
        for (left_at, left_id), (right_at, right_id) in zip(points, points[1:]):
            minutes = (right_at - left_at).total_seconds() / 60.0
            if minutes <= 0:
                continue
            gaps.append(
                OpenGap(
                    start=left_at,
                    end=right_at,
                    minutes=minutes,
                    before_event_id=left_id,
                    after_event_id=right_id,
                )
            )
    return gaps
