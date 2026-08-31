"""Tender fold of the log. Disposable rows; payment events still own the win."""

from __future__ import annotations

from dataclasses import dataclass

from event_spine.events import Event
from event_spine.project import Ticket, project
from event_spine.stats import percentile


# Fixed emit order for winning tenders (card before cash before leftovers).
_TENDER_ORDER = ("card", "cash", "unpaid", "open")


@dataclass(frozen=True, slots=True)
class TenderRow:
    tender: str
    tickets: int
    closed: int
    open: int
    revenue_cents: int
    dwell_p50_min: float | None


def tender_of(source: object) -> str:
    """Map a ticket to its winning tender, unpaid, or open.

    Winning tender is the method on the last successful PaymentCaptured
    for a closed and paid ticket (typically card or cash). Unpaid is
    closed without a full capture. Open is still in the bay. Accepts a
    Ticket or anything with closed / paid / payments attributes. Junk
    stays empty so the renderer can print —.
    """
    if source is None:
        return ""
    if isinstance(source, Ticket):
        ticket = source
    else:
        closed = getattr(source, "closed", None)
        paid = getattr(source, "paid", None)
        payments = getattr(source, "payments", None)
        if closed is None or paid is None or payments is None:
            return ""
        ticket = source  # duck-typed below

    if not ticket.closed:
        return "open"
    if not ticket.paid:
        return "unpaid"
    wins = [p for p in ticket.payments if p.ok]
    if not wins:
        return ""
    method = str(wins[-1].method or "")
    return method


def _tender_rank(tender: str) -> int:
    try:
        return _TENDER_ORDER.index(tender)
    except ValueError:
        return len(_TENDER_ORDER)


def by_tender(events: list[Event]) -> list[TenderRow]:
    """Rebuild one row per winning tender from the ticket projection.

    Tender is classified from the last successful PaymentCaptured on
    closed+paid tickets via tender_of; closed unpaid and still-open
    tickets land in unpaid / open. Revenue is the sum of closed ticket
    line-item totals (integer cents). dwell_p50_min is Hyndman-Fan
    type 7 over closed-ticket dwell minutes. Still-open tickets count
    toward tickets/open but not revenue or dwell. Sorted by
    revenue_cents desc, then card / cash / unpaid / open (empty last).
    """
    tickets = project(events)
    by: dict[str, list[Ticket]] = {}
    for ticket in tickets.values():
        by.setdefault(tender_of(ticket), []).append(ticket)

    rows: list[TenderRow] = []
    for tender, group in by.items():
        closed = [t for t in group if t.closed]
        dwells = [
            (t.closed_at - t.opened_at).total_seconds() / 60.0
            for t in closed
            if t.closed_at is not None
        ]
        rows.append(
            TenderRow(
                tender=tender,
                tickets=len(group),
                closed=len(closed),
                open=len(group) - len(closed),
                revenue_cents=sum(t.total_cents for t in closed),
                dwell_p50_min=percentile(dwells, 0.50),
            )
        )
    rows.sort(
        key=lambda row: (-row.revenue_cents, _tender_rank(row.tender), row.tender)
    )
    return rows
