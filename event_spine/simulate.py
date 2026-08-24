"""Seeded day at Splitrock Lube. Four planted irregularities, then normal noise."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from event_spine.events import Event, EventType

SHOP = "Splitrock Lube"
DAY = date(2026, 3, 14)

# Everyday menu. Prices in cents.
MENU: list[tuple[str, str, int]] = [
    ("OIL-CONV", "Conventional 5W-30", 3999),
    ("OIL-SYN", "Synthetic 5W-30", 6999),
    ("OIL-FS", "Full synthetic 0W-20", 8499),
    ("FIL-OIL", "Oil filter", 1299),
    ("FIL-AIR", "Engine air filter", 2499),
    ("FIL-CAB", "Cabin air filter", 2999),
    ("WIP-STD", "Wiper blades", 2199),
    ("FLD-COOL", "Coolant top-off", 899),
    ("INSP", "Multi-point inspection", 0),
]

OILS = [row for row in MENU if row[0].startswith("OIL-")]
BY_SKU = {row[0]: row for row in MENU}

# The whale ticket. Real services, wrong scale for this shop's baseline.
WHALE: list[tuple[str, str, int]] = [
    ("TRN-FLUSH", "Transmission flush", 18900),
    ("DIFF-FLUID", "Differential fluid service", 14900),
    ("BRK-FLUSH", "Brake fluid flush", 12900),
    ("FIL-OIL", "Oil filter", 1299),
    ("OIL-FS", "Full synthetic 0W-20", 8499),
]

VEHICLES = [
    "2018 Honda Civic",
    "2021 Toyota RAV4",
    "2015 Ford F-150",
    "2019 Subaru Outback",
    "2012 Chevy Silverado",
    "2016 Mazda CX-5",
    "2020 Hyundai Tucson",
    "2014 Jeep Grand Cherokee",
    "2017 BMW 328i",
    "2013 Honda CR-V",
    "2022 Ford Escape",
    "2011 Toyota Camry",
]

FAIL_REASONS = ("declined", "timeout", "network")


@dataclass(frozen=True, slots=True)
class SimConfig:
    seed: int = 42
    day: date = DAY
    open_hour: int = 7
    close_hour: int = 19
    mean_gap_s: float = 14 * 60
    # Optional hole in the arrival stream. Off by default so seed 42
    # stays the four-plant day; tests turn this on to plant a lunch
    # silence or a dead register.
    silent_gap_hour: int | None = None
    silent_gap_minute: int = 0
    silent_gap_minutes: int = 0


class _Ids:
    def __init__(self) -> None:
        self._e = 0
        self._t = 0

    def event(self) -> str:
        self._e += 1
        return f"e_{self._e:04d}"

    def ticket(self) -> str:
        self._t += 1
        return f"t_{self._t:03d}"


def simulate_day(config: SimConfig | None = None) -> list[Event]:
    """One shop day. Same seed → same events, including the four plants."""
    cfg = config or SimConfig()
    rng = random.Random(cfg.seed)
    ids = _Ids()
    events: list[Event] = []
    clock = datetime(cfg.day.year, cfg.day.month, cfg.day.day, cfg.open_hour, 6, tzinfo=UTC)
    close = datetime(cfg.day.year, cfg.day.month, cfg.day.day, cfg.close_hour, 0, tzinfo=UTC)

    # Planted times. Detectors should find these without being told.
    fleet_at = clock.replace(hour=11, minute=30, second=0)
    whale_at = clock.replace(hour=14, minute=18, second=0)
    outage_at = clock.replace(hour=16, minute=3, second=0)
    dwell_at = clock.replace(hour=9, minute=42, second=0)

    arrivals = _arrivals(rng, clock, close, cfg.mean_gap_s)
    arrivals.extend(fleet_at + timedelta(seconds=12 * i) for i in range(8))
    arrivals.extend(outage_at + timedelta(seconds=50 * i) for i in range(6))
    arrivals.append(whale_at)
    arrivals.append(dwell_at)
    arrivals.sort()
    if cfg.silent_gap_hour is not None and cfg.silent_gap_minutes > 0:
        gap_start = clock.replace(
            hour=cfg.silent_gap_hour,
            minute=cfg.silent_gap_minute,
            second=0,
            microsecond=0,
        )
        gap_end = gap_start + timedelta(minutes=cfg.silent_gap_minutes)
        arrivals = [t for t in arrivals if not (gap_start <= t < gap_end)]

    outage_end = outage_at + timedelta(minutes=7)
    whale_used = False
    dwell_used = False

    for arrival in arrivals:
        if arrival >= close:
            break
        is_whale = arrival == whale_at and not whale_used
        if is_whale:
            whale_used = True
        is_dwell = arrival == dwell_at and not dwell_used
        if is_dwell:
            dwell_used = True
        in_outage = outage_at <= arrival <= outage_end
        ticket_events, clock_end = _one_ticket(
            rng,
            ids,
            start=arrival,
            whale=is_whale,
            force_fail=in_outage,
            long_dwell=is_dwell,
        )
        events.extend(ticket_events)
        if clock_end > close + timedelta(minutes=40):
            break

    events.sort(key=lambda e: (e.occurred_at, e.ticket_id, e.type.value))
    return [
        Event(
            event_id=f"e_{i:04d}",
            type=event.type,
            occurred_at=event.occurred_at,
            ticket_id=event.ticket_id,
            payload=event.payload,
        )
        for i, event in enumerate(events, start=1)
    ]


def _arrivals(
    rng: random.Random,
    start: datetime,
    close: datetime,
    mean_gap_s: float,
) -> list[datetime]:
    times: list[datetime] = []
    t = start
    while t < close:
        hour = t.hour
        # Slightly tighter gaps at lunch and the after-work wave.
        scale = mean_gap_s
        if 11 <= hour < 13:
            scale *= 0.75
        elif 16 <= hour < 18:
            scale *= 0.7
        gap = max(90.0, rng.expovariate(1.0 / scale))
        t = t + timedelta(seconds=gap)
        if t < close:
            times.append(t)
    return times


def _one_ticket(
    rng: random.Random,
    ids: _Ids,
    *,
    start: datetime,
    whale: bool,
    force_fail: bool,
    long_dwell: bool = False,
) -> tuple[list[Event], datetime]:
    ticket_id = ids.ticket()
    t = start
    events: list[Event] = [
        Event(
            event_id=ids.event(),
            type=EventType.TICKET_OPENED,
            occurred_at=t,
            ticket_id=ticket_id,
            payload={
                "bay": str(rng.randint(1, 3)),
                "vehicle": rng.choice(VEHICLES),
                "shop": SHOP,
            },
        )
    ]

    items = _items(rng, whale=whale)
    for sku, description, unit_cents in items:
        t = t + timedelta(seconds=rng.randint(20, 90))
        events.append(
            Event(
                event_id=ids.event(),
                type=EventType.LINE_ITEM_ADDED,
                occurred_at=t,
                ticket_id=ticket_id,
                payload={
                    "sku": sku,
                    "description": description,
                    "qty": 1,
                    "unit_cents": unit_cents,
                },
            )
        )

    total = sum(unit for _, _, unit in items)
    method = "cash" if rng.random() < 0.12 else "card"

    # Bay sits on one car for hours. Close is still a fact; the gap is the tell.
    if long_dwell:
        t = t + timedelta(hours=3, minutes=7)

    # Organic decline ~2.5% on cards. Outage: fail once or twice, then usually capture.
    attempts = 0
    captured = False
    if method == "card" and (force_fail or rng.random() < 0.025):
        fails = 2 if force_fail else 1
        for _ in range(fails):
            t = t + timedelta(seconds=rng.randint(8, 25))
            attempts += 1
            events.append(
                Event(
                    event_id=ids.event(),
                    type=EventType.PAYMENT_FAILED,
                    occurred_at=t,
                    ticket_id=ticket_id,
                    payload={
                        "method": method,
                        "amount_cents": total,
                        "attempt": attempts,
                        "reason": "network" if force_fail else rng.choice(FAIL_REASONS),
                    },
                )
            )
        # Outage still settles on a retry so the day can close; the burst is the fails.
        if force_fail and rng.random() < 0.15:
            pass
        else:
            captured = True
    else:
        captured = True

    if captured:
        t = t + timedelta(seconds=rng.randint(6, 20))
        attempts += 1
        events.append(
            Event(
                event_id=ids.event(),
                type=EventType.PAYMENT_CAPTURED,
                occurred_at=t,
                ticket_id=ticket_id,
                payload={
                    "method": method,
                    "amount_cents": total,
                    "attempt": attempts,
                },
            )
        )
        t = t + timedelta(seconds=rng.randint(4, 15))
        events.append(
            Event(
                event_id=ids.event(),
                type=EventType.TICKET_CLOSED,
                occurred_at=t,
                ticket_id=ticket_id,
                payload={"total_cents": total},
            )
        )

    return events, t


def _items(rng: random.Random, *, whale: bool) -> list[tuple[str, str, int]]:
    if whale:
        return list(WHALE)
    oil = rng.choice(OILS)
    items = [oil, BY_SKU["FIL-OIL"]]
    if rng.random() < 0.85:
        items.append(BY_SKU["INSP"])
    extras = [
        row
        for row in MENU
        if row[0] not in {oil[0], "FIL-OIL", "INSP"} and not row[0].startswith("OIL-")
    ]
    for extra in extras:
        if rng.random() < 0.22:
            items.append(extra)
    return items
