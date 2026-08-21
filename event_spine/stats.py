"""Day-level numbers from the log: tickets, fail rate, dwell percentiles, detector hits."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from event_spine.detect import detect
from event_spine.events import PAYMENT_TYPES, Event, EventType
from event_spine.project import closed_in_order, project

# Stable print order. Any extra detector name sorts after these.
DETECTORS = (
    "ticket_total",
    "payment_failure",
    "payment_failure_cusum",
    "payment_failure_ewma",
    "velocity",
    "ticket_dwell",
    "concurrent_open",
)


@dataclass(frozen=True, slots=True)
class DayStats:
    events: int
    tickets: int
    closed: int
    payments: int
    failures: int
    fail_rate: float
    dwell_p50_min: float | None
    dwell_p95_min: float | None
    detector_hits: tuple[tuple[str, int], ...]


def percentile(values: Sequence[float], p: float) -> float | None:
    """Linear interpolation (Hyndman-Fan type 7). p in [0, 1]. None if empty."""
    if not (0.0 <= p <= 1.0):
        raise ValueError(f"percentile p must be in [0, 1], got {p}")
    n = len(values)
    if n == 0:
        return None
    ordered = sorted(values)
    if n == 1:
        return float(ordered[0])
    idx = (n - 1) * p
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return float(ordered[lo])
    frac = idx - lo
    return float(ordered[lo]) * (1.0 - frac) + float(ordered[hi]) * frac


def dwell_minutes(events: list[Event]) -> list[float]:
    """Closed-ticket dwell in minutes, close order. Open tickets are skipped."""
    out: list[float] = []
    for ticket in closed_in_order(project(events)):
        if ticket.closed_at is None:
            continue
        out.append((ticket.closed_at - ticket.opened_at).total_seconds() / 60.0)
    return out


def summarize(events: list[Event]) -> DayStats:
    tickets = project(events)
    payments = [e for e in events if e.type in PAYMENT_TYPES]
    fails = sum(1 for e in payments if e.type is EventType.PAYMENT_FAILED)
    dwells = dwell_minutes(events)
    hits = Counter(a.detector for a in detect(events))
    names = list(DETECTORS)
    for name in sorted(hits):
        if name not in names:
            names.append(name)
    return DayStats(
        events=len(events),
        tickets=len(tickets),
        closed=sum(1 for t in tickets.values() if t.closed),
        payments=len(payments),
        failures=fails,
        fail_rate=(fails / len(payments)) if payments else 0.0,
        dwell_p50_min=percentile(dwells, 0.50),
        dwell_p95_min=percentile(dwells, 0.95),
        detector_hits=tuple((name, hits.get(name, 0)) for name in names),
    )
