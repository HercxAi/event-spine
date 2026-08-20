"""Human-readable stdout for a day's log and whatever the detectors flagged."""

from __future__ import annotations

import json
import math
from typing import Any

from event_spine.detect import Anomaly, detect, fmt_cents
from event_spine.events import Event, EventType
from event_spine.project import Ticket, project
from event_spine.simulate import SHOP


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
        lines.append(f"{i}. {a.detector}  z={score}")
        lines.append(f"   {a.summary}")
        shown = a.event_ids[:8]
        extra = f" +{len(a.event_ids) - 8} more" if len(a.event_ids) > 8 else ""
        lines.append(f"   events: {', '.join(shown)}{extra}")
        if i != len(anomalies):
            lines.append("")
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


_DETECTOR_ORDER = (
    "ticket_total",
    "payment_failure",
    "velocity",
    "ticket_dwell",
)


def render_summary(events: list[Event], anomalies: list[Anomaly] | None = None) -> str:
    """One-page recap: tickets, revenue, payments, anomalies grouped by detector."""
    if anomalies is None:
        anomalies = detect(events)
    tickets = project(events)
    closed = [t for t in tickets.values() if t.closed]
    revenue = sum(t.total_cents for t in closed)
    captured = sum(1 for e in events if e.type is EventType.PAYMENT_CAPTURED)
    failed = sum(1 for e in events if e.type is EventType.PAYMENT_FAILED)
    day = events[0].occurred_at.date().isoformat() if events else "—"

    lines = [
        f"{SHOP}  ·  {day}",
        "",
        f"tickets   {len(tickets)}",
        f"revenue   {fmt_cents(revenue)}",
        f"payments  {captured} captured / {failed} failed",
        "",
    ]
    if not anomalies:
        lines.append("no anomalies")
        return "\n".join(lines) + "\n"

    grouped: dict[str, list[Anomaly]] = {}
    for anomaly in anomalies:
        grouped.setdefault(anomaly.detector, []).append(anomaly)

    names = [name for name in _DETECTOR_ORDER if name in grouped]
    names.extend(name for name in grouped if name not in _DETECTOR_ORDER)

    lines.append("anomalies")
    for name in names:
        lines.append(f"  {name}")
        for anomaly in grouped[name]:
            lines.append(f"    {anomaly.summary}")
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
