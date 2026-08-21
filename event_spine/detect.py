"""Six statistical detectors. Named math, no model weights."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from event_spine.events import PAYMENT_TYPES, Event, EventType
from event_spine.project import closed_in_order, project


@dataclass(frozen=True, slots=True)
class Anomaly:
    detector: str
    score: float
    at: datetime
    summary: str
    event_ids: tuple[str, ...]
    ticket_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


def sample_mean_std(values: Sequence[float]) -> tuple[float, float] | None:
    """Sample mean and stddev (Bessel, n-1). None if n < 2."""
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    var = sum((x - mean) ** 2 for x in values) / (n - 1)
    return mean, math.sqrt(var)


def zscore(value: float, baseline: Sequence[float]) -> float | None:
    """(x - mean) / s. Zero-variance baseline: 0 if x == mean, else +inf."""
    stats = sample_mean_std(baseline)
    if stats is None:
        return None
    mean, std = stats
    if std == 0.0:
        return 0.0 if value == mean else math.inf
    return (value - mean) / std


def proportion_z(successes: int, n: int, p0: float) -> float | None:
    """One-sample proportion z-test: (p - p0) / sqrt(p0(1-p0)/n)."""
    if n <= 0 or not (0.0 < p0 < 1.0):
        return None
    p = successes / n
    se = math.sqrt(p0 * (1.0 - p0) / n)
    if se == 0.0:
        return None
    return (p - p0) / se


def tabular_cusum_k(p0: float, *, shift: float = 0.5) -> float | None:
    """Montgomery reference value: k = p0 + shift·σ, σ = √(p0(1−p0))."""
    if not (0.0 < p0 < 1.0):
        return None
    return p0 + shift * math.sqrt(p0 * (1.0 - p0))


def cusum_highside(
    values: Sequence[float],
    *,
    k: float,
    h: float,
) -> list[tuple[int, int, float]]:
    """High-side tabular CUSUM (Page 1954 / Montgomery).

    S_t = max(0, S_{t−1} + x_t − k). Alarm when S_t ≥ h.
    Returns (decision_index, change_index, S) once per excursion:
    latches until S returns to 0 so a run is one change-point.
    change_index is the start of the current positive run
    (last time S was 0, next observation).
    """
    if h <= 0:
        return []
    found: list[tuple[int, int, float]] = []
    statistic = 0.0
    change: int | None = None
    latched = False
    for i, value in enumerate(values):
        statistic = max(0.0, statistic + float(value) - k)
        if statistic == 0.0:
            change = None
            latched = False
            continue
        if change is None:
            change = i
        if not latched and statistic >= h:
            found.append((i, change, statistic))
            latched = True
    return found


def detect_ticket_totals(
    events: list[Event],
    *,
    window: int = 16,
    z_thresh: float = 2.8,
    min_samples: int = 8,
) -> list[Anomaly]:
    """High-side z-score of a closed ticket's line-item sum vs prior tickets."""
    tickets = closed_in_order(project(events))
    close_by_id = {
        e.ticket_id: e
        for e in events
        if e.type is EventType.TICKET_CLOSED
    }
    found: list[Anomaly] = []
    history: list[float] = []
    for ticket in tickets:
        total = float(ticket.total_cents)
        baseline = history[-window:]
        if len(baseline) >= min_samples:
            z = zscore(total, baseline)
            if z is not None and z >= z_thresh:
                stats = sample_mean_std(baseline)
                assert stats is not None
                mean, std = stats
                close = close_by_id[ticket.ticket_id]
                found.append(
                    Anomaly(
                        detector="ticket_total",
                        score=z,
                        at=ticket.closed_at or ticket.opened_at,
                        summary=(
                            f"{ticket.ticket_id} {fmt_cents(ticket.total_cents)} "
                            f"vs baseline {fmt_cents(int(round(mean)))} "
                            f"(σ={fmt_cents(int(round(std)))})"
                        ),
                        event_ids=(close.event_id,),
                        ticket_id=ticket.ticket_id,
                        details={
                            "total_cents": ticket.total_cents,
                            "baseline_mean_cents": mean,
                            "baseline_std_cents": std,
                            "window": len(baseline),
                        },
                    )
                )
        history.append(total)
    return found


def detect_payment_failures(
    events: list[Event],
    *,
    window_s: int = 8 * 60,
    min_payments: int = 5,
    z_thresh: float = 2.5,
    prior: float = 0.03,
    baseline_frac: float = 0.4,
) -> list[Anomaly]:
    """Sliding-window proportion z-test of PaymentFailed vs morning baseline."""
    payments = [e for e in events if e.type in PAYMENT_TYPES]
    if len(payments) < min_payments:
        return []

    split = max(min_payments, int(len(payments) * baseline_frac))
    early = payments[:split]
    early_fails = sum(1 for e in early if e.type is EventType.PAYMENT_FAILED)
    p0 = early_fails / len(early)
    # Keep the standard error defined; a 0% morning still uses the prior.
    p0 = min(max(p0, prior), 0.5)

    raw: list[Anomaly] = []
    right = 0
    for left, start in enumerate(payments):
        end_at = start.occurred_at + timedelta(seconds=window_s)
        while right < len(payments) and payments[right].occurred_at <= end_at:
            right += 1
        window = payments[left:right]
        if len(window) < min_payments:
            continue
        fails = [e for e in window if e.type is EventType.PAYMENT_FAILED]
        z = proportion_z(len(fails), len(window), p0)
        if z is None or z < z_thresh:
            continue
        raw.append(
            Anomaly(
                detector="payment_failure",
                score=z,
                at=start.occurred_at,
                summary=(
                    f"{len(fails)}/{len(window)} failed "
                    f"{_hhmm(start.occurred_at)}–{_hhmm(window[-1].occurred_at)} "
                    f"(baseline {p0:.1%})"
                ),
                event_ids=tuple(e.event_id for e in fails),
                details={
                    "failures": len(fails),
                    "payments": len(window),
                    "p0": p0,
                    "window_start": start.occurred_at.isoformat(),
                    "window_end": window[-1].occurred_at.isoformat(),
                },
            )
        )
    return _peak_windows(raw)


def detect_payment_failure_cusum(
    events: list[Event],
    *,
    baseline_until_hour: int = 14,
    min_baseline: int = 8,
    prior: float = 0.03,
    h: float = 4.0,
    k_shift: float = 0.5,
) -> list[Anomaly]:
    """High-side tabular CUSUM on PaymentFailed / PaymentCaptured.

    Morning baseline is payments before 14:00 UTC so the 16:03 plant
    does not leak into p0. x=1 on fail, x=0 on capture. k = p0 + ½σ
    (Bernoulli), alarm at h. One anomaly per excursion.
    """
    payments = [e for e in events if e.type in PAYMENT_TYPES]
    if len(payments) < min_baseline:
        return []

    early = [e for e in payments if e.occurred_at.hour < baseline_until_hour]
    if len(early) < min_baseline:
        return []
    early_fails = sum(1 for e in early if e.type is EventType.PAYMENT_FAILED)
    p0 = early_fails / len(early)
    p0 = min(max(p0, prior), 0.5)
    k = tabular_cusum_k(p0, shift=k_shift)
    if k is None:
        return []

    xs = [1.0 if e.type is EventType.PAYMENT_FAILED else 0.0 for e in payments]
    found: list[Anomaly] = []
    for decision_i, change_i, statistic in cusum_highside(xs, k=k, h=h):
        decision = payments[decision_i]
        change = payments[change_i]
        run = payments[change_i : decision_i + 1]
        found.append(
            Anomaly(
                detector="payment_failure_cusum",
                score=statistic,
                at=decision.occurred_at,
                summary=(
                    f"change at {_hhmm(change.occurred_at)}  "
                    f"S={statistic:.2f} ≥ h={h:g} "
                    f"(k={k:.3f}, baseline {p0:.1%})"
                ),
                event_ids=tuple(e.event_id for e in run),
                details={
                    "p0": p0,
                    "k": k,
                    "h": h,
                    "S": statistic,
                    "change_event_id": change.event_id,
                    "decision_event_id": decision.event_id,
                    "change_at": change.occurred_at.isoformat(),
                    "baseline_until_hour": baseline_until_hour,
                    "baseline_payments": len(early),
                    "baseline_failures": early_fails,
                },
            )
        )
    return found


def detect_velocity(
    events: list[Event],
    *,
    bin_minutes: int = 5,
    lookback: int = 8,
    z_thresh: float = 2.8,
    min_bins: int = 6,
    min_count: int = 5,
    std_floor: float = 0.5,
) -> list[Anomaly]:
    """Z-score of TicketOpened counts in fixed bins, empty bins included."""
    opens = [e for e in events if e.type is EventType.TICKET_OPENED]
    if len(opens) < 2:
        return []

    first = _floor(opens[0].occurred_at, bin_minutes)
    last = _floor(opens[-1].occurred_at, bin_minutes)
    step = timedelta(minutes=bin_minutes)
    bins: list[tuple[datetime, list[Event]]] = []
    cursor = first
    while cursor <= last:
        bins.append((cursor, []))
        cursor += step
    index = {start: i for i, (start, _) in enumerate(bins)}
    for event in opens:
        key = _floor(event.occurred_at, bin_minutes)
        bins[index[key]][1].append(event)

    found: list[Anomaly] = []
    counts = [float(len(group)) for _, group in bins]
    for i, (start, group) in enumerate(bins):
        if i < min_bins:
            continue
        baseline = counts[max(0, i - lookback) : i]
        if len(baseline) < 2:
            continue
        if counts[i] < min_count:
            continue
        stats = sample_mean_std(baseline)
        if stats is None:
            continue
        mean, std = stats
        std = max(std, std_floor)
        z = (counts[i] - mean) / std
        if z < z_thresh:
            continue
        found.append(
            Anomaly(
                detector="velocity",
                score=z,
                at=start,
                summary=(
                    f"{len(group)} tickets "
                    f"{_hhmm(start)}–{_hhmm(start + step)} "
                    f"(baseline {mean:.1f}/{bin_minutes}min)"
                ),
                event_ids=tuple(e.event_id for e in group),
                details={
                    "count": len(group),
                    "baseline_mean": mean,
                    "baseline_std": std,
                    "bin_minutes": bin_minutes,
                },
            )
        )
    return found


def detect_ticket_dwell(
    events: list[Event],
    *,
    window: int = 16,
    z_thresh: float = 2.8,
    min_samples: int = 8,
    as_of: datetime | None = None,
) -> list[Anomaly]:
    """High-side z-score of ticket dwell in minutes vs prior closed tickets.

    Closed tickets are scored at close. Still-open tickets are scored at
    as_of (default: last event) so a bay that never closed still flags.
    """
    projected = project(events)
    tickets = closed_in_order(projected)
    open_by_id = {
        e.ticket_id: e
        for e in events
        if e.type is EventType.TICKET_OPENED
    }
    close_by_id = {
        e.ticket_id: e
        for e in events
        if e.type is EventType.TICKET_CLOSED
    }
    found: list[Anomaly] = []
    history: list[float] = []
    for ticket in tickets:
        if ticket.closed_at is None:
            continue
        dwell_min = (ticket.closed_at - ticket.opened_at).total_seconds() / 60.0
        baseline = history[-window:]
        if len(baseline) >= min_samples:
            z = zscore(dwell_min, baseline)
            if z is not None and z >= z_thresh:
                stats = sample_mean_std(baseline)
                assert stats is not None
                mean, std = stats
                opened = open_by_id[ticket.ticket_id]
                close = close_by_id[ticket.ticket_id]
                found.append(
                    Anomaly(
                        detector="ticket_dwell",
                        score=z,
                        at=ticket.closed_at,
                        summary=(
                            f"{ticket.ticket_id} {dwell_min:.0f}min on bay {ticket.bay or '?'} "
                            f"vs baseline {mean:.0f}min (σ={std:.1f})"
                        ),
                        event_ids=(opened.event_id, close.event_id),
                        ticket_id=ticket.ticket_id,
                        details={
                            "dwell_minutes": dwell_min,
                            "baseline_mean_minutes": mean,
                            "baseline_std_minutes": std,
                            "window": len(baseline),
                            "bay": ticket.bay,
                        },
                    )
                )
        history.append(dwell_min)

    now = as_of
    if now is None and events:
        now = max(e.occurred_at for e in events)
    if now is not None:
        still_open = sorted(
            (t for t in projected.values() if not t.closed),
            key=lambda t: (t.opened_at, t.ticket_id),
        )
        for ticket in still_open:
            dwell_min = (now - ticket.opened_at).total_seconds() / 60.0
            if dwell_min < 0:
                continue
            baseline = history[-window:]
            if len(baseline) < min_samples:
                continue
            z = zscore(dwell_min, baseline)
            if z is None or z < z_thresh:
                continue
            stats = sample_mean_std(baseline)
            assert stats is not None
            mean, std = stats
            opened = open_by_id[ticket.ticket_id]
            found.append(
                Anomaly(
                    detector="ticket_dwell",
                    score=z,
                    at=now,
                    summary=(
                        f"{ticket.ticket_id} still open {dwell_min:.0f}min "
                        f"on bay {ticket.bay or '?'} "
                        f"vs baseline {mean:.0f}min (σ={std:.1f})"
                    ),
                    event_ids=(opened.event_id,),
                    ticket_id=ticket.ticket_id,
                    details={
                        "dwell_minutes": dwell_min,
                        "baseline_mean_minutes": mean,
                        "baseline_std_minutes": std,
                        "window": len(baseline),
                        "bay": ticket.bay,
                        "open": True,
                        "as_of": now.isoformat(),
                    },
                )
            )
    return found


def detect_concurrent_open(
    events: list[Event],
    *,
    window: int = 16,
    z_thresh: float = 2.8,
    min_samples: int = 8,
    min_count: int = 4,
) -> list[Anomaly]:
    """High-side z-score of tickets open at once vs prior open/close snapshots."""
    steps = [
        e
        for e in events
        if e.type in (EventType.TICKET_OPENED, EventType.TICKET_CLOSED)
    ]
    steps.sort(key=lambda e: (e.occurred_at, e.ticket_id, e.type.value))

    found: list[Anomaly] = []
    history: list[float] = []
    open_events: dict[str, Event] = {}
    concurrent = 0
    for event in steps:
        if event.type is EventType.TICKET_OPENED:
            concurrent += 1
            open_events[event.ticket_id] = event
        else:
            concurrent = max(0, concurrent - 1)
            open_events.pop(event.ticket_id, None)

        baseline = history[-window:]
        if len(baseline) >= min_samples and concurrent >= min_count:
            z = zscore(float(concurrent), baseline)
            if z is not None and z >= z_thresh:
                stats = sample_mean_std(baseline)
                assert stats is not None
                mean, std = stats
                justifying = tuple(
                    e.event_id
                    for e in sorted(
                        open_events.values(),
                        key=lambda e: (e.occurred_at, e.event_id),
                    )
                )
                found.append(
                    Anomaly(
                        detector="concurrent_open",
                        score=z,
                        at=event.occurred_at,
                        summary=(
                            f"{concurrent} open at {_hhmm(event.occurred_at)} "
                            f"vs baseline {mean:.1f} (σ={std:.1f})"
                        ),
                        event_ids=justifying,
                        details={
                            "concurrent": concurrent,
                            "baseline_mean": mean,
                            "baseline_std": std,
                            "window": len(baseline),
                        },
                    )
                )
        history.append(float(concurrent))
    return _peak_windows(
        found,
        rank=lambda a: (-int(a.details.get("concurrent", 0)), -a.score, a.at),
    )


def detect(events: list[Event]) -> list[Anomaly]:
    anomalies = [
        *detect_ticket_totals(events),
        *detect_payment_failures(events),
        *detect_payment_failure_cusum(events),
        *detect_velocity(events),
        *detect_ticket_dwell(events),
        *detect_concurrent_open(events),
    ]
    anomalies.sort(key=lambda a: (a.at, -a.score, a.detector))
    return anomalies


def _peak_windows(
    candidates: list[Anomaly],
    *,
    rank: Callable[[Anomaly], tuple[Any, ...]] | None = None,
) -> list[Anomaly]:
    """Keep the highest-ranked hit; drop ones that share an event id."""
    ranked = sorted(candidates, key=rank or (lambda a: (-a.score, a.at)))
    kept: list[Anomaly] = []
    used: set[str] = set()
    for anomaly in ranked:
        if used.intersection(anomaly.event_ids):
            continue
        kept.append(anomaly)
        used.update(anomaly.event_ids)
    kept.sort(key=lambda a: a.at)
    return kept


def _floor(dt: datetime, minutes: int) -> datetime:
    minute = (dt.minute // minutes) * minutes
    return dt.replace(minute=minute, second=0, microsecond=0)


def _hhmm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def fmt_cents(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}${cents // 100:,}.{cents % 100:02d}"
