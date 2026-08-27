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
from event_spine.sku import SkuRow, by_sku
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
    if stats.total_p50_cents is None or stats.total_p95_cents is None:
        totals = "total  —"
    else:
        totals = (
            f"total p50 {fmt_cents(int(round(stats.total_p50_cents)))}  "
            f"p95 {fmt_cents(int(round(stats.total_p95_cents)))}"
        )
    lines = [
        f"{SHOP}  ·  {day}  ·  {stats.events} events  ·  {stats.tickets} tickets",
        (
            f"{stats.closed} closed  ·  "
            f"fail rate {stats.fail_rate:.1%} ({stats.failures}/{stats.payments})  ·  "
            f"{dwell}"
        ),
        totals,
        "",
        "detector hits",
    ]
    for name, count in stats.detector_hits:
        lines.append(f"  {name:<24} {count}")
    return "\n".join(lines) + "\n"


def render_stats_json(events: list[Event], stats: DayStats | None = None) -> str:
    """JSON object for the day summary. Human stdout stays the default."""
    if stats is None:
        stats = summarize(events)
    day = events[0].occurred_at.date().isoformat() if events else None
    payload: dict[str, Any] = {
        "shop": SHOP,
        "day": day,
        "events": stats.events,
        "tickets": stats.tickets,
        "closed": stats.closed,
        "payments": stats.payments,
        "failures": stats.failures,
        "fail_rate": stats.fail_rate,
        "dwell_p50_min": stats.dwell_p50_min,
        "dwell_p95_min": stats.dwell_p95_min,
        "total_p50_cents": stats.total_p50_cents,
        "total_p95_cents": stats.total_p95_cents,
        "detector_hits": [
            {"detector": name, "count": count} for name, count in stats.detector_hits
        ],
    }
    return json.dumps(payload, indent=2) + "\n"


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


def render_brief_json(events: list[Event], brief: DayBrief | None = None) -> str:
    """JSON object for the daily ops brief. Human stdout stays the default."""
    if brief is None:
        brief = from_log(events)
    day = events[0].occurred_at.date().isoformat() if events else None
    leftover = [
        {
            "ticket_id": ticket.ticket_id,
            "opened_at": ticket.opened_at.isoformat(),
            "bay": ticket.bay or None,
            "total_cents": ticket.total_cents,
            "paid": ticket.paid,
        }
        for ticket in brief.leftover
    ]
    payload: dict[str, Any] = {
        "shop": SHOP,
        "day": day,
        "events": brief.events,
        "tickets_opened": brief.tickets_opened,
        "tickets_closed": brief.tickets_closed,
        "payments_captured": brief.payments_captured,
        "payments_failed": brief.payments_failed,
        "revenue_cents": brief.revenue_cents,
        "leftover": leftover,
        "detector_hits": [
            {"detector": name, "count": count} for name, count in brief.detector_hits
        ],
    }
    return json.dumps(payload, indent=2) + "\n"


def render_detect_json(anomalies: list[Anomaly]) -> str:
    """JSON array of anomalies. Human stdout stays the default elsewhere."""
    return json.dumps([_anomaly_row(a) for a in anomalies], indent=2) + "\n"


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


def render_hours_json(events: list[Event], bins: list[HourBin] | None = None) -> str:
    """JSON object for the hourly fold. Human stdout stays the default."""
    if bins is None:
        bins = by_hour(events)
    if events:
        day = events[0].occurred_at.date().isoformat()
    elif bins:
        day = bins[0].hour.date().isoformat()
    else:
        day = None
    payload: dict[str, Any] = {
        "shop": SHOP,
        "day": day,
        "events": len(events),
        "hours": [
            {
                "hour": row.hour.isoformat(),
                "tickets_opened": row.tickets_opened,
                "payments_captured": row.payments_captured,
                "payments_failed": row.payments_failed,
                "revenue_cents": row.revenue_cents,
                "peak_open": row.peak_open,
            }
            for row in bins
        ],
    }
    return json.dumps(payload, indent=2) + "\n"


def render_sku(events: list[Event], rows: list[SkuRow] | None = None) -> str:
    """One line per SKU rebuilt from LineItemAdded, highest ext_cents first."""
    if rows is None:
        rows = by_sku(events)
    day = events[0].occurred_at.date().isoformat() if events else "—"
    total_ext = sum(row.ext_cents for row in rows)
    total_qty = sum(row.qty for row in rows)
    lines = [
        f"{SHOP}  ·  {day}  ·  {len(events)} events  ·  sku",
        f"{len(rows)} skus  ·  {total_qty} units  ·  ext {fmt_cents(total_ext)}",
        "",
    ]
    if not rows:
        lines.append("no line items")
        return "\n".join(lines) + "\n"
    for row in rows:
        lines.append(
            f"{row.sku:<12} {row.description:<28} "
            f"lines {row.lines:<3} qty {row.qty:<4} "
            f"{fmt_cents(row.ext_cents):>10}"
        )
    return "\n".join(lines) + "\n"


def render_sku_json(events: list[Event], rows: list[SkuRow] | None = None) -> str:
    """JSON object for the SKU fold. Human stdout stays the default."""
    if rows is None:
        rows = by_sku(events)
    day = events[0].occurred_at.date().isoformat() if events else None
    payload: dict[str, Any] = {
        "shop": SHOP,
        "day": day,
        "events": len(events),
        "skus": [
            {
                "sku": row.sku,
                "description": row.description,
                "lines": row.lines,
                "qty": row.qty,
                "ext_cents": row.ext_cents,
            }
            for row in rows
        ],
    }
    return json.dumps(payload, indent=2) + "\n"


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


def _anomaly_row(a: Anomaly) -> dict[str, Any]:
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


def render_gaps_json(events: list[Event], anomalies: list[Anomaly] | None = None) -> str:
    """JSON object for the silent-gap fold. Human stdout stays the default."""
    if anomalies is None:
        anomalies = detect_silent_gap(events)
    if events:
        day = events[0].occurred_at.date().isoformat()
    else:
        day = None
    payload: dict[str, Any] = {
        "shop": SHOP,
        "day": day,
        "events": len(events),
        "threshold_minutes": SILENT_GAP_MINUTES,
        "shop_open": SHOP_OPEN_HOUR,
        "shop_close": SHOP_CLOSE_HOUR,
        "gaps": [_anomaly_row(a) for a in anomalies],
    }
    return json.dumps(payload, indent=2) + "\n"


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


def render_replay_json(
    tickets: dict[str, Ticket],
    *,
    events: list[Event] | None = None,
    limit: int | None = None,
) -> str:
    """JSON object for the ticket projection. Human stdout stays the default."""
    ordered = sorted(tickets.values(), key=lambda tk: (tk.opened_at, tk.ticket_id))
    if limit is not None:
        ordered = ordered[:limit]
    if events:
        day = events[0].occurred_at.date().isoformat()
        n_events = len(events)
    elif ordered:
        day = ordered[0].opened_at.date().isoformat()
        n_events = None
    else:
        day = None
        n_events = 0 if events is not None else None
    payload: dict[str, Any] = {
        "shop": SHOP,
        "day": day,
        "events": n_events,
        "tickets": [
            {
                "ticket_id": ticket.ticket_id,
                "opened_at": ticket.opened_at.isoformat(),
                "closed_at": ticket.closed_at.isoformat() if ticket.closed_at else None,
                "bay": ticket.bay or None,
                "vehicle": ticket.vehicle,
                "total_cents": ticket.total_cents,
                "paid": ticket.paid,
                "closed": ticket.closed,
                "items": [
                    {
                        "sku": item.sku,
                        "description": item.description,
                        "qty": item.qty,
                        "unit_cents": item.unit_cents,
                        "ext_cents": item.ext_cents,
                    }
                    for item in ticket.items
                ],
                "payments": [
                    {
                        "ok": payment.ok,
                        "method": payment.method,
                        "amount_cents": payment.amount_cents,
                        "at": payment.at.isoformat(),
                        "event_id": payment.event_id,
                        "reason": payment.reason,
                    }
                    for payment in ticket.payments
                ],
            }
            for ticket in ordered
        ],
    }
    return json.dumps(payload, indent=2) + "\n"

