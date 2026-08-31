"""Outcome fold of the log. Disposable rows; payment events still own the path."""

from __future__ import annotations

from dataclasses import dataclass

from event_spine.events import Event
from event_spine.project import Ticket, project
from event_spine.stats import percentile


# Fixed emit order for payment journeys (clean before recovered before unpaid).
_OUTCOME_ORDER = ("clean", "recovered", "unpaid", "open")


@dataclass(frozen=True, slots=True)
class OutcomeRow:
    outcome: str
    tickets: int
    closed: int
    open: int
    revenue_cents: int
    dwell_p50_min: float | None


def outcome_of(source: object) -> str:
    """Map a ticket's payment path to clean, recovered, unpaid, or open.

    Clean is closed and paid with no PaymentFailed. Recovered is closed
    and paid after at least one failure. Unpaid is closed without a full
    capture. Open is still in the bay. Accepts a Ticket or anything with
    closed / paid / payments attributes. Junk stays empty so the
    renderer can print —.
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
    failures = sum(1 for p in ticket.payments if not p.ok)
    if ticket.paid:
        return "recovered" if failures else "clean"
    return "unpaid"


def _outcome_rank(outcome: str) -> int:
    try:
        return _OUTCOME_ORDER.index(outcome)
    except ValueError:
        return len(_OUTCOME_ORDER)


def by_outcome(events: list[Event]) -> list[OutcomeRow]:
    """Rebuild one row per payment journey from the ticket projection.

    Outcome is classified from closed / paid / PaymentFailed via
    outcome_of on each ticket. Revenue is the sum of closed ticket
    line-item totals (integer cents). dwell_p50_min is Hyndman-Fan
    type 7 over closed-ticket dwell minutes. Still-open tickets count
    toward tickets/open but not revenue or dwell. Sorted by
    revenue_cents desc, then clean / recovered / unpaid / open
    (empty last).
    """
    tickets = project(events)
    by: dict[str, list[Ticket]] = {}
    for ticket in tickets.values():
        by.setdefault(outcome_of(ticket), []).append(ticket)

    rows: list[OutcomeRow] = []
    for outcome, group in by.items():
        closed = [t for t in group if t.closed]
        dwells = [
            (t.closed_at - t.opened_at).total_seconds() / 60.0
            for t in closed
            if t.closed_at is not None
        ]
        rows.append(
            OutcomeRow(
                outcome=outcome,
                tickets=len(group),
                closed=len(closed),
                open=len(group) - len(closed),
                revenue_cents=sum(t.total_cents for t in closed),
                dwell_p50_min=percentile(dwells, 0.50),
            )
        )
    rows.sort(
        key=lambda row: (-row.revenue_cents, _outcome_rank(row.outcome), row.outcome)
    )
    return rows
