"""Detectors over the event log. Named math, no model weights."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from event_spine.events import PAYMENT_TYPES, Event, EventType
from event_spine.gaps import shop_open_gaps
from event_spine.hours import SHOP_CLOSE_HOUR, SHOP_OPEN_HOUR
from event_spine.project import closed_in_order, project

# Longer than the seeded day's natural holes (max ~45min), short enough
# that a skipped lunch rush or a dead register is obvious.
SILENT_GAP_MINUTES = 45


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


# Φ^{-1}(0.75). MAD / 0.6745 estimates σ for Gaussian data.
MAD_CONSISTENCY = 0.6745


def median(values: Sequence[float]) -> float | None:
    """Sample median. Even n: average of the two central order stats."""
    n = len(values)
    if n == 0:
        return None
    ordered = sorted(values)
    mid = n // 2
    if n % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def mad(values: Sequence[float]) -> float | None:
    """Median absolute deviation from the sample median. None if empty."""
    med = median(values)
    if med is None:
        return None
    return median([abs(x - med) for x in values])


def modified_zscore(value: float, baseline: Sequence[float]) -> float | None:
    """Iglewicz-Hoaglin modified z: 0.6745 (x − median) / MAD.

    None if n < 2. Zero MAD: 0 if x == median, else +inf.
    """
    if len(baseline) < 2:
        return None
    med = median(baseline)
    spread = mad(baseline)
    if med is None or spread is None:
        return None
    if spread == 0.0:
        return 0.0 if value == med else math.inf
    return MAD_CONSISTENCY * (value - med) / spread


def percentile(values: Sequence[float], p: float) -> float | None:
    """Hyndman-Fan type 7 (linear interpolation). None if empty."""
    n = len(values)
    if n == 0:
        return None
    if not 0.0 <= p <= 1.0:
        raise ValueError(f"percentile p must be in [0, 1], got {p}")
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


def tukey_highside(value: float, baseline: Sequence[float]) -> float | None:
    """High-side Tukey fence score: (x − Q3) / IQR.

    Inner fence is score >= 1.5. None if n < 4.
    Zero IQR: 0 if x == Q3, else +inf.
    """
    if len(baseline) < 4:
        return None
    q1 = percentile(baseline, 0.25)
    q3 = percentile(baseline, 0.75)
    if q1 is None or q3 is None:
        return None
    spread = q3 - q1
    if spread == 0.0:
        return 0.0 if value == q3 else math.inf
    return (value - q3) / spread


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


def ewma_ucl(mu0: float, sigma: float, *, lam: float, L: float) -> float | None:
    """Asymptotic EWMA limit: μ0 + L·σ·√(λ/(2−λ))."""
    if L <= 0 or sigma <= 0 or not (0.0 < lam <= 1.0):
        return None
    return mu0 + L * sigma * math.sqrt(lam / (2.0 - lam))


def ewma_highside(
    values: Sequence[float],
    *,
    mu0: float,
    lam: float,
    L: float,
    sigma: float,
) -> list[tuple[int, int, float]]:
    """High-side EWMA (Roberts 1959).

    Z_t = λ x_t + (1−λ) Z_{t−1}, Z_0 = μ0.
    Alarm when Z_t ≥ UCL = μ0 + L·σ·√(λ/(2−λ)).
    Returns (decision_index, change_index, Z) once per excursion:
    latches until Z returns to μ0 so a run is one change-point.
    change_index is the start of the current climb
    (last time Z was at μ0, next observation).
    """
    ucl = ewma_ucl(mu0, sigma, lam=lam, L=L)
    if ucl is None:
        return []
    found: list[tuple[int, int, float]] = []
    statistic = mu0
    change: int | None = None
    latched = False
    for i, value in enumerate(values):
        statistic = lam * float(value) + (1.0 - lam) * statistic
        if statistic <= mu0:
            change = None
            latched = False
            continue
        if change is None:
            change = i
        if not latched and statistic >= ucl:
            found.append((i, change, statistic))
            latched = True
    return found


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


def detect_ticket_totals_mad(
    events: list[Event],
    *,
    window: int = 16,
    z_thresh: float = 3.5,
    min_samples: int = 8,
) -> list[Anomaly]:
    """High-side Iglewicz-Hoaglin modified z-score of a closed ticket total.

    M = 0.6745 (x − median) / MAD versus the previous N tickets.
    Sample σ is pulled by a prior whale (masking); the MAD is not.
    Threshold 3.5 is the published cutoff.
    """
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
            z = modified_zscore(total, baseline)
            if z is not None and z >= z_thresh:
                med = median(baseline)
                spread = mad(baseline)
                assert med is not None and spread is not None
                close = close_by_id[ticket.ticket_id]
                found.append(
                    Anomaly(
                        detector="ticket_total_mad",
                        score=z,
                        at=ticket.closed_at or ticket.opened_at,
                        summary=(
                            f"{ticket.ticket_id} {fmt_cents(ticket.total_cents)} "
                            f"vs median {fmt_cents(int(round(med)))} "
                            f"(MAD={fmt_cents(int(round(spread)))})"
                        ),
                        event_ids=(close.event_id,),
                        ticket_id=ticket.ticket_id,
                        details={
                            "total_cents": ticket.total_cents,
                            "baseline_median_cents": med,
                            "baseline_mad_cents": spread,
                            "window": len(baseline),
                        },
                    )
                )
        history.append(total)
    return found


def detect_ticket_totals_iqr(
    events: list[Event],
    *,
    window: int = 16,
    k: float = 1.5,
    min_samples: int = 8,
) -> list[Anomaly]:
    """High-side Tukey inner fence on a closed ticket total.

    Score = (x − Q3) / IQR versus the previous N tickets. Alarm at
    k = 1.5 (the published inner fence). Quartiles are Hyndman-Fan
    type 7. A prior whale that inflates sample σ still sits in one
    tail of the box; the fence does not walk with it the way mean+σ
    does.
    """
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
            score = tukey_highside(total, baseline)
            if score is not None and score >= k:
                q1 = percentile(baseline, 0.25)
                q3 = percentile(baseline, 0.75)
                assert q1 is not None and q3 is not None
                close = close_by_id[ticket.ticket_id]
                found.append(
                    Anomaly(
                        detector="ticket_total_iqr",
                        score=score,
                        at=ticket.closed_at or ticket.opened_at,
                        summary=(
                            f"{ticket.ticket_id} {fmt_cents(ticket.total_cents)} "
                            f"vs Q3 {fmt_cents(int(round(q3)))} "
                            f"(IQR={fmt_cents(int(round(q3 - q1)))})"
                        ),
                        event_ids=(close.event_id,),
                        ticket_id=ticket.ticket_id,
                        details={
                            "total_cents": ticket.total_cents,
                            "baseline_q1_cents": q1,
                            "baseline_q3_cents": q3,
                            "baseline_iqr_cents": q3 - q1,
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


def detect_payment_failure_ewma(
    events: list[Event],
    *,
    baseline_until_hour: int = 14,
    min_baseline: int = 8,
    prior: float = 0.03,
    lam: float = 0.1,
    L: float = 3.0,
) -> list[Anomaly]:
    """Roberts EWMA on PaymentFailed / PaymentCaptured.

    Same morning Bernoulli p0 as the CUSUM (payments before 14:00 UTC
    so the 16:03 plant stays out of the baseline). x=1 on fail, x=0 on
    capture. Z_t = λx_t + (1−λ)Z_{t−1}, Z_0 = p0, λ = 0.1. Alarm at
    the asymptotic UCL with L = 3. One anomaly per excursion. A small
    persistent rise in the fail rate moves Z before CUSUM's S reaches h.
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
    sigma = math.sqrt(p0 * (1.0 - p0))
    ucl = ewma_ucl(p0, sigma, lam=lam, L=L)
    if ucl is None:
        return []
    sigma_z = sigma * math.sqrt(lam / (2.0 - lam))

    xs = [1.0 if e.type is EventType.PAYMENT_FAILED else 0.0 for e in payments]
    found: list[Anomaly] = []
    for decision_i, change_i, statistic in ewma_highside(
        xs, mu0=p0, lam=lam, L=L, sigma=sigma
    ):
        decision = payments[decision_i]
        change = payments[change_i]
        run = payments[change_i : decision_i + 1]
        standardized = (statistic - p0) / sigma_z
        found.append(
            Anomaly(
                detector="payment_failure_ewma",
                score=standardized,
                at=decision.occurred_at,
                summary=(
                    f"change at {_hhmm(change.occurred_at)}  "
                    f"Z={statistic:.3f} ≥ UCL={ucl:.3f} "
                    f"(λ={lam:g}, L={L:g}, baseline {p0:.1%})"
                ),
                event_ids=tuple(e.event_id for e in run),
                details={
                    "p0": p0,
                    "lambda": lam,
                    "L": L,
                    "Z": statistic,
                    "UCL": ucl,
                    "sigma": sigma,
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


def detect_silent_gap(
    events: list[Event],
    *,
    threshold_min: float = SILENT_GAP_MINUTES,
    open_hour: int = SHOP_OPEN_HOUR,
    close_hour: int = SHOP_CLOSE_HOUR,
) -> list[Anomaly]:
    """Flag shop-hour stretches with no TicketOpened longer than threshold.

    Rebuilt from the log: walk TicketOpened during [open, close), including
    the open→first and last→close legs. After-hours silence is ignored.
    Score is the gap length in minutes.
    """
    found: list[Anomaly] = []
    for gap in shop_open_gaps(events, open_hour=open_hour, close_hour=close_hour):
        if gap.minutes < threshold_min:
            continue
        event_ids = tuple(
            eid for eid in (gap.before_event_id, gap.after_event_id) if eid
        )
        found.append(
            Anomaly(
                detector="silent_gap",
                score=gap.minutes,
                at=gap.start,
                summary=(
                    f"{gap.minutes:.0f}min with no TicketOpened "
                    f"{_hhmm(gap.start)}–{_hhmm(gap.end)} "
                    f"(threshold {threshold_min:g}min)"
                ),
                event_ids=event_ids,
                details={
                    "gap_minutes": gap.minutes,
                    "threshold_minutes": threshold_min,
                    "window_start": gap.start.isoformat(),
                    "window_end": gap.end.isoformat(),
                    "open_hour": open_hour,
                    "close_hour": close_hour,
                    "before_event_id": gap.before_event_id,
                    "after_event_id": gap.after_event_id,
                },
            )
        )
    return found


def detect_declined_abandoned(events: list[Event]) -> list[Anomaly]:
    """Flag a ticket with PaymentFailed and no later PaymentCaptured.

    Rebuild per ticket from the log. A decline that later captures is a
    retry, not a walk-off. Still-open at the end of the log, or
    TicketClosed without a capture after the fail, both count.
    Score is the unpaid line-item total in dollars.
    """
    by_ticket: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        by_ticket[event.ticket_id].append(event)

    found: list[Anomaly] = []
    for ticket_id, rows in by_ticket.items():
        rows.sort(key=lambda e: (e.occurred_at, e.event_id))
        last_fail: Event | None = None
        captured_after = False
        opened: Event | None = None
        closed: Event | None = None
        fails: list[Event] = []
        total_cents = 0
        for event in rows:
            if event.type is EventType.TICKET_OPENED:
                opened = event
            elif event.type is EventType.LINE_ITEM_ADDED:
                qty = int(event.payload.get("qty", 1))
                total_cents += qty * int(event.payload.get("unit_cents", 0))
            elif event.type is EventType.PAYMENT_FAILED:
                last_fail = event
                captured_after = False
                fails.append(event)
            elif event.type is EventType.PAYMENT_CAPTURED:
                if last_fail is not None:
                    captured_after = True
            elif event.type is EventType.TICKET_CLOSED:
                closed = event
        if last_fail is None or captured_after:
            continue
        justifying = [e.event_id for e in fails]
        if opened is not None:
            justifying.insert(0, opened.event_id)
        if closed is not None:
            justifying.append(closed.event_id)
        dollars = total_cents / 100.0
        reason = str(last_fail.payload.get("reason") or "failed")
        state = "still open" if closed is None else "closed without capture"
        found.append(
            Anomaly(
                detector="declined_abandoned",
                score=dollars,
                at=last_fail.occurred_at,
                summary=(
                    f"{ticket_id} {reason} {fmt_cents(total_cents)} "
                    f"and walked — {state}"
                ),
                event_ids=tuple(justifying),
                ticket_id=ticket_id,
                details={
                    "total_cents": total_cents,
                    "failed_payments": len(fails),
                    "open": closed is None,
                    "reason": reason,
                    "last_fail_event_id": last_fail.event_id,
                    "last_fail_at": last_fail.occurred_at.isoformat(),
                },
            )
        )
    found.sort(key=lambda a: (a.at, a.ticket_id or ""))
    return found


def detect(events: list[Event]) -> list[Anomaly]:
    anomalies = [
        *detect_ticket_totals(events),
        *detect_ticket_totals_mad(events),
        *detect_ticket_totals_iqr(events),
        *detect_payment_failures(events),
        *detect_payment_failure_cusum(events),
        *detect_payment_failure_ewma(events),
        *detect_velocity(events),
        *detect_ticket_dwell(events),
        *detect_concurrent_open(events),
        *detect_silent_gap(events),
        *detect_declined_abandoned(events),
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
