from __future__ import annotations

import json
import unittest
from datetime import timedelta

from event_spine.events import EventType
from event_spine.project import project
from event_spine.report import render_tender, render_tender_json
from event_spine.simulate import SimConfig, simulate_day
from event_spine.stats import percentile
from event_spine.tender import TenderRow, by_tender, tender_of
from tests.helpers import at, ev, ticket_flow


def _cash_flow(
    ticket_id: str,
    opened,
    total_cents: int,
    *,
    prefix: str,
    dwell=None,
    fail: bool = False,
):
    """Like ticket_flow but the winning capture is cash."""
    events = ticket_flow(
        ticket_id,
        opened,
        total_cents,
        prefix=prefix,
        fail=fail,
        dwell=dwell,
    )
    out = []
    for e in events:
        if e.type is EventType.PAYMENT_CAPTURED:
            payload = dict(e.payload)
            payload["method"] = "cash"
            out.append(
                ev(e.event_id, e.type, e.occurred_at, e.ticket_id, **payload)
            )
        else:
            out.append(e)
    return out


class TenderOfTests(unittest.TestCase):
    def test_classifies_winning_tenders(self) -> None:
        card = project(ticket_flow("t_card", at(8), 4000, prefix="c"))["t_card"]
        self.assertEqual(tender_of(card), "card")

        cash = project(_cash_flow("t_cash", at(9), 5000, prefix="h"))["t_cash"]
        self.assertEqual(tender_of(cash), "cash")

        recovered_cash = project(
            _cash_flow("t_r", at(10), 6000, prefix="r", fail=True)
        )["t_r"]
        self.assertEqual(tender_of(recovered_cash), "cash")

        open_events = [
            ev(
                "o1",
                EventType.TICKET_OPENED,
                at(10),
                "t_o",
                bay="1",
                vehicle="2018 Honda Civic",
            ),
            ev(
                "o2",
                EventType.LINE_ITEM_ADDED,
                at(10, 1),
                "t_o",
                sku="OIL-CONV",
                description="oil",
                qty=1,
                unit_cents=3999,
            ),
        ]
        opened = project(open_events)["t_o"]
        self.assertEqual(tender_of(opened), "open")

        unpaid_events = [
            ev(
                "u1",
                EventType.TICKET_OPENED,
                at(11),
                "t_u",
                bay="2",
                vehicle="2015 Ford F-150",
            ),
            ev(
                "u2",
                EventType.LINE_ITEM_ADDED,
                at(11, 1),
                "t_u",
                sku="OIL-SYN",
                description="oil",
                qty=1,
                unit_cents=6999,
            ),
            ev(
                "u3",
                EventType.PAYMENT_FAILED,
                at(11, 2),
                "t_u",
                method="card",
                amount_cents=6999,
                reason="declined",
            ),
            ev("u4", EventType.TICKET_CLOSED, at(11, 3), "t_u", total_cents=6999),
        ]
        unpaid = project(unpaid_events)["t_u"]
        self.assertEqual(tender_of(unpaid), "unpaid")

    def test_uses_last_successful_capture(self) -> None:
        events = [
            ev(
                "e1",
                EventType.TICKET_OPENED,
                at(8),
                "t1",
                bay="1",
                vehicle="2018 Honda Civic",
            ),
            ev(
                "e2",
                EventType.LINE_ITEM_ADDED,
                at(8, 1),
                "t1",
                sku="OIL-CONV",
                description="oil",
                qty=1,
                unit_cents=4000,
            ),
            ev(
                "e3",
                EventType.PAYMENT_CAPTURED,
                at(8, 2),
                "t1",
                method="card",
                amount_cents=1000,
            ),
            ev(
                "e4",
                EventType.PAYMENT_CAPTURED,
                at(8, 3),
                "t1",
                method="cash",
                amount_cents=3000,
            ),
            ev("e5", EventType.TICKET_CLOSED, at(8, 4), "t1", total_cents=4000),
        ]
        ticket = project(events)["t1"]
        self.assertTrue(ticket.paid)
        self.assertEqual(tender_of(ticket), "cash")

    def test_missing_stays_empty(self) -> None:
        self.assertEqual(tender_of(None), "")
        self.assertEqual(tender_of(""), "")
        self.assertEqual(tender_of(42), "")


class TenderFoldTests(unittest.TestCase):
    def test_groups_by_classified_tender(self) -> None:
        unpaid_events = [
            ev(
                "u1",
                EventType.TICKET_OPENED,
                at(11),
                "t_u",
                bay="2",
                vehicle="2015 Ford F-150",
            ),
            ev(
                "u2",
                EventType.LINE_ITEM_ADDED,
                at(11, 1),
                "t_u",
                sku="OIL-SYN",
                description="oil",
                qty=1,
                unit_cents=6999,
            ),
            ev(
                "u3",
                EventType.PAYMENT_FAILED,
                at(11, 2),
                "t_u",
                method="card",
                amount_cents=6999,
                reason="declined",
            ),
            ev("u4", EventType.TICKET_CLOSED, at(11, 3), "t_u", total_cents=6999),
        ]
        events = [
            *ticket_flow(
                "t_a",
                at(8),
                4000,
                prefix="a",
                dwell=timedelta(minutes=30),
            ),
            *_cash_flow(
                "t_b",
                at(12),
                8000,
                prefix="b",
                dwell=timedelta(minutes=60),
            ),
            *unpaid_events,
            ev(
                "o1",
                EventType.TICKET_OPENED,
                at(16),
                "t_open",
                bay="1",
                vehicle="2017 BMW 328i",
            ),
        ]
        rows = {row.tender: row for row in by_tender(events)}
        self.assertEqual(set(rows), {"card", "cash", "unpaid", "open"})

        card = rows["card"]
        self.assertEqual(card.tickets, 1)
        self.assertEqual(card.closed, 1)
        self.assertEqual(card.open, 0)
        self.assertEqual(card.revenue_cents, 4000)
        self.assertAlmostEqual(card.dwell_p50_min or 0.0, 30.0)

        cash = rows["cash"]
        self.assertEqual(cash.tickets, 1)
        self.assertEqual(cash.closed, 1)
        self.assertEqual(cash.open, 0)
        self.assertEqual(cash.revenue_cents, 8000)
        self.assertAlmostEqual(cash.dwell_p50_min or 0.0, 60.0)

        unpaid = rows["unpaid"]
        self.assertEqual(unpaid.tickets, 1)
        self.assertEqual(unpaid.closed, 1)
        self.assertEqual(unpaid.open, 0)
        self.assertEqual(unpaid.revenue_cents, 6999)

        opened = rows["open"]
        self.assertEqual(opened.tickets, 1)
        self.assertEqual(opened.closed, 0)
        self.assertEqual(opened.open, 1)
        self.assertEqual(opened.revenue_cents, 0)
        self.assertIsNone(opened.dwell_p50_min)

    def test_open_ticket_skips_revenue_and_dwell(self) -> None:
        events = [
            ev(
                "e1",
                EventType.TICKET_OPENED,
                at(8),
                "t1",
                bay="3",
                vehicle="2021 Toyota RAV4",
            ),
            ev(
                "e2",
                EventType.LINE_ITEM_ADDED,
                at(8, 1),
                "t1",
                sku="OIL-CONV",
                description="oil",
                qty=1,
                unit_cents=3999,
            ),
        ]
        rows = by_tender(events)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].tender, "open")
        self.assertEqual(rows[0].tickets, 1)
        self.assertEqual(rows[0].closed, 0)
        self.assertEqual(rows[0].open, 1)
        self.assertEqual(rows[0].revenue_cents, 0)
        self.assertIsNone(rows[0].dwell_p50_min)

    def test_empty_log_is_empty_list(self) -> None:
        self.assertEqual(by_tender([]), [])

    def test_sorts_by_revenue_then_tender_order(self) -> None:
        events = [
            *ticket_flow("t_z", at(8), 1000, prefix="z"),
            *ticket_flow("t_a", at(9), 1000, prefix="a"),
            *_cash_flow("t_m", at(12), 5000, prefix="m"),
        ]
        self.assertEqual(
            [row.tender for row in by_tender(events)],
            ["cash", "card"],
        )
        self.assertEqual(by_tender(events)[1].tickets, 2)

    def test_tie_breaks_card_cash_unpaid_open(self) -> None:
        unpaid_events = [
            ev(
                "u1",
                EventType.TICKET_OPENED,
                at(11),
                "t_u",
                bay="2",
                vehicle="2015 Ford F-150",
            ),
            ev(
                "u2",
                EventType.LINE_ITEM_ADDED,
                at(11, 1),
                "t_u",
                sku="OIL-SYN",
                description="oil",
                qty=1,
                unit_cents=2000,
            ),
            ev(
                "u3",
                EventType.PAYMENT_FAILED,
                at(11, 2),
                "t_u",
                method="card",
                amount_cents=2000,
                reason="timeout",
            ),
            ev("u4", EventType.TICKET_CLOSED, at(11, 3), "t_u", total_cents=2000),
        ]
        events = [
            *_cash_flow("t_a", at(16), 2000, prefix="a"),
            *ticket_flow("t_m", at(8), 2000, prefix="m"),
            *unpaid_events,
            ev(
                "o1",
                EventType.TICKET_OPENED,
                at(17),
                "t_open",
                bay="1",
                vehicle="2017 BMW 328i",
            ),
        ]
        self.assertEqual(
            [row.tender for row in by_tender(events)],
            ["card", "cash", "unpaid", "open"],
        )

    def test_seeded_day_covers_all_tickets(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        rows = by_tender(events)
        tickets = project(events)
        self.assertEqual(sum(row.tickets for row in rows), len(tickets))
        expected = sum(t.total_cents for t in tickets.values() if t.closed)
        self.assertEqual(sum(row.revenue_cents for row in rows), expected)
        self.assertGreater(expected, 0)
        leftover = sum(1 for t in tickets.values() if not t.closed)
        self.assertEqual(sum(row.open for row in rows), leftover)
        names = {row.tender for row in rows}
        self.assertIn("card", names)
        self.assertIn("cash", names)
        self.assertNotIn("", names)

        by: dict[str, list] = {}
        for ticket in tickets.values():
            by.setdefault(tender_of(ticket), []).append(ticket)
        manual: list[TenderRow] = []
        for tender, group in by.items():
            closed = [t for t in group if t.closed]
            dwells = [
                (t.closed_at - t.opened_at).total_seconds() / 60.0
                for t in closed
                if t.closed_at is not None
            ]
            manual.append(
                TenderRow(
                    tender=tender,
                    tickets=len(group),
                    closed=len(closed),
                    open=len(group) - len(closed),
                    revenue_cents=sum(t.total_cents for t in closed),
                    dwell_p50_min=percentile(dwells, 0.50),
                )
            )
        order = {"card": 0, "cash": 1, "unpaid": 2, "open": 3}
        manual.sort(
            key=lambda row: (
                -row.revenue_cents,
                order.get(row.tender, 99),
                row.tender,
            )
        )
        self.assertEqual(rows, manual)


class TenderRenderTests(unittest.TestCase):
    def test_human_and_json_match_fold(self) -> None:
        events = [
            *ticket_flow("t_a", at(8), 6999, prefix="a"),
            *_cash_flow("t_b", at(16), 1299, prefix="b"),
        ]
        rows = by_tender(events)
        text = render_tender(events, rows)
        self.assertIn("tender", text)
        self.assertIn("2 tenders", text)
        self.assertIn("2 tickets", text)
        self.assertIn("card", text)
        self.assertIn("cash", text)
        payload = json.loads(render_tender_json(events, rows))
        self.assertEqual(payload["events"], len(events))
        self.assertIn("shop", payload)
        self.assertIn("day", payload)
        self.assertIn("tenders", payload)
        self.assertEqual(
            [
                (r["tender"], r["tickets"], r["revenue_cents"])
                for r in payload["tenders"]
            ],
            [(row.tender, row.tickets, row.revenue_cents) for row in rows],
        )


if __name__ == "__main__":
    unittest.main()
