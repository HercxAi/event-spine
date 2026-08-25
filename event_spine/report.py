"""Human-readable stdout for a day's log and whatever the detectors flagged."""

from __future__ import annotations

import json
import math
from typing import Any

from event_spine.brief import DayBrief, from_log
from event_spine.detect import SILENT_GAP_MINUTES, Anomaly, detect, detect_silent_gap, fmt_cents
from event_spine.events import Event, EventType
from event_spine.hours import SHOP_CLOSE_HOUR, SHOP_OPEN_HOUR, HourBin, by_hour
from event_spine.project import Ticket, project
from event_spine.simulate import SHOP
from event_spine.stats import DayStats, summarize


def render_detect(events: list[Event], anomalies: list[Anomaly] | None = None) -> str:
    if anomalies is None:
        anomalies = detect(events)
    tickets = project(events)
    closed = [t for t in tickets.values() if t.closed]
    revenue = sum(t.total_cents for t in closed)
    payments = [
        e for e in events if e.type in (EventType.PAYMENT_CAPTURED, EventType.PAYMENT_FAILED)
    ]
    fails = sum(1 for e in payments if e.type is EventType.PAYMENT_FAILED)
    fail_rate = (fails / len(payments)) if payments else 0.0
    avg = (revenue // len(closed)) if closed else 0
    day = events[0].occurred_at.date().isoformat() if events else "—"

    lines = [
        f"{SHOP}  ·  {day}  ·  {len(events)} events  ·  {len(tickets)} tickets",
        (
            f"revenue {fmt_cents(revenue)}  ·  "
            f"avg ticket {fmt_cents(avg)}  ·  "
            f"fail rate {fail_rate:.1%} ({fails}/{len(payments)})"
        ),
        "",
    ]
    if not anomalies:
        lines.append("no anomalies")
        return "\n".join(lines) + "\n"

    lines.append(f"{len(anomalies)} anomal{'y' if len(anomalies) == 1 else 'ies'}")
    lines.append("")
    for i, a in enumerate(anomalies, start=1):
        score = "inf" if a.score == float("inf") else f"{a.score:.2f}"
        if a.detector == "payment_failure_cusum":
            label = "S"
        elif a.detector == "payment_failure_ewma":
            label = "Z"
        elif a.detector == "ticket_total_mad":
            label = "M"
        elif a.detector == "silent_gap":
            label = "min"
        else:
            label = "z"
        lines.append(f"{i}. {a.detector}  {label}={score}")
        lines.append(f"   {a.summary}")
        shown = a.event_ids[:8]
        extra = f" +{len(a.event_ids) - 8} more" if len(a.event_ids) > 8 else ""
        lines.append(f"   events: {', '.join(shown)}{extra}")
        if i != len(anomalies):
            lines.append("")
    return "\n".join(lines) + "\n"


def render_stats(events: list[Event], stats: DayStats | None = None) -> str:
    """One-screen summary: volume, fail rate, dwell percentiles, detector hits."""
    if stats is None:
        stats = summarize(events)
    day = events[0].occurred_at.date().isoformat() if events else "—"
    if stats.dwell_p50_min is None or stats.dwell_p95_min is None:
        dwell = "dwell  —"
    else:
        dwell = (
            f"dwell p50 {stats.dwell_p50_min:.1f}min  "
            f"p95 {stats.dwell_p95_min:.1f}min"
        )
    lines = [
        f"{SHOP}  ·  {day}  ·  {stats.events} events  ·  {stats.tickets} tickets",
        (
            f"{stats.closed} closed  ·  "
            f"fail rate {stats.fail_rate:.1%} ({stats.failures}/{stats.payments})  ·  "
            f"{dwell}"
        ),
        "",
        "detector hits",
    ]
    for name, count in stats.detector_hits:
        lines.append(f"  {name:<24} {count}")
    return "\n".join(lines) + "\n"


def render_brief(events: list[Event], brief: DayBrief | None = None) -> str:
    """One-page daily ops view rebuilt from the log."""
    if brief is None:
        brief = from_log(events)
    day = events[0].occurred_at.date().isoformat() if events else "—"
    lines = [
        f"{SHOP}  ·  {day}  ·  {brief.events} events  ·  daily brief",
        (
            f"opened {brief.tickets_opened}  ·  "
            f"closed {brief.tickets_closed}  ·  "
            f"leftover open {len(brief.leftover)}"
        ),
        (
            f"captured {brief.payments_captured}  ·  "
            f"failed {brief.payments_failed}  ·  "
            f"revenue {fmt_cents(brief.revenue_cents)}"
        ),
    ]
    if brief.leftover:
        lines.append("")
        for ticket in brief.leftover:
            paid = "paid" if ticket.paid else "unpaid"
            lines.append(
                f"  {ticket.ticket_id}  {ticket.opened_at.strftime('%H:%M')}  "
                f"bay {ticket.bay or '?'}  {fmt_cents(ticket.total_cents)}  {paid}"
            )
    lines.append("")
    lines.append("detector hits")
    for name, count in brief.detector_hits:
        lines.append(f"  {name:<24} {count}")
    return "\n".join(lines) + "\n"


def render_detect_json(anomalies: list[Anomaly]) -> str:
    """JSON array of anomalies. Human stdout stays the default elsewhere."""

    def row(a: Anomaly) -> dict[str, Any]:
        score: float | str = a.score
        if not math.isfinite(a.score):
            score = "Infinity" if a.score > 0 else "-Infinity"
        return {
            "detector": a.detector,
            "score": score,
            "at": a.at.isoformat(),
            "summary": a.summary,
            "event_ids": list(a.event_ids),
            "ticket_id": a.ticket_id,
            "details": a.details,
        }

    return json.dumps([row(a) for a in anomalies], indent=2) + "\n"


def render_hours(events: list[Event], bins: list[HourBin] | None = None) -> str:
    """One line per shop-open hour, rebuilt from the log."""
    if bins is None:
        bins = by_hour(events)
    if events:
        day = events[0].occurred_at.date().isoformat()
    elif bins:
        day = bins[0].hour.date().isoformat()
    else:
        day = "—"
    lines = [
        f"{SHOP}  ·  {day}  ·  {len(events)} events  ·  hourly",
        "",
    ]
    for row in bins:
        lines.append(
            f"{row.hour:%H:%M}  "
            f"opened {row.tickets_opened}  "
            f"captured {row.payments_captured}  "
            f"failed {row.payments_failed}  "
            f"revenue {fmt_cents(row.revenue_cents)}  "
            f"peak {row.peak_open}"
        )
    return "\n".join(lines) + "\n"


def render_gaps(events: list[Event], anomalies: list[Anomaly] | None = None) -> str:
    """Silent shop-hour stretches with no TicketOpened, rebuilt from the log."""
    if anomalies is None:
        anomalies = detect_silent_gap(events)
    if events:
        day = events[0].occurred_at.date().isoformat()
    else:
        day = "—"
    lines = [
        f"{SHOP}  ·  {day}  ·  {len(events)} events  ·  silent gaps",
        (
            f"threshold {SILENT_GAP_MINUTES:g}min during "
            f"{SHOP_OPEN_HOUR:02d}:00–{SHOP_CLOSE_HOUR:02d}:00"
        ),
        "",
    ]
    if not anomalies:
        lines.append("no silent gaps")
        return "\n".join(lines) + "\n"
    for i, a in enumerate(anomalies, start=1):
        lines.append(f"{i}. {a.summary}")
        if a.event_ids:
            shown = a.event_ids[:8]
            extra = f" +{len(a.event_ids) - 8} more" if len(a.event_ids) > 8 else ""
            lines.append(f"   events: {', '.join(shown)}{extra}")
        if i != len(anomalies):
            lines.append("")
    return "\n".join(lines) + "\n"


def render_replay(tickets: dict[str, Ticket], *, limit: int | None = None) -> str:
    ordered = sorted(tickets.values(), key=lambda t: (t.opened_at, t.ticket_id))
    if limit is not None:
        ordered = ordered[:limit]
    lines: list[str] = []
    for ticket in ordered:
        state = "closed" if ticket.closed else "open"
        paid = "paid" if ticket.paid else "unpaid"
        lines.append(
            f"{ticket.ticket_id}  {ticket.opened_at.strftime('%H:%M')}  "
            f"bay {ticket.bay or '?'}  {fmt_cents(ticket.total_cents)}  "
            f"{paid}  {state}  {ticket.vehicle}"
        )
        for item in ticket.items:
            lines.append(
                f"  {item.sku:<10} {item.description:<32} {fmt_cents(item.ext_cents):>10}"
            )
        for payment in ticket.payments:
            tag = "captured" if payment.ok else f"failed:{payment.reason or '?'}"
            lines.append(
                f"  {tag:<16} {payment.method:<16} {fmt_cents(payment.amount_cents):>10}"
            )
        lines.append("")
    return "\n".join(lines)
