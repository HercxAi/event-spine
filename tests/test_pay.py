from __future__ import annotations

import json
import unittest

from event_spine.events import EventType
from event_spine.pay import by_method
from event_spine.report import render_pay, render_pay_json
from event_spine.simulate import SimConfig, simulate_day
from tests.helpers import at, ev, ticket_flow


class PayFoldTests(unittest.TestCase):
    def test_groups_card_and_cash(self) -> None:
        events = [
            *ticket_flow("t_a", at(8), 4000, prefix="a"),
            ev("c1", EventType.TICKET_OPENED, at(9), "t_b", bay="1", vehicle="x"),
            ev(
                "c2",
                EventType.LINE_ITEM_ADDED,
                at(9, 1),
                "t_b",
                sku="OIL-SYN",
                description="syn",
                qty=1,
                unit_cents=8000,
            ),
            ev(
                "c3",
                EventType.PAYMENT_CAPTURED,
                at(9, 2),
                "t_b",
                method="cash",
                amount_cents=8000,
            ),
            ev("c4", EventType.TICKET_CLOSED, at(9, 3), "t_b", total_cents=8000),
            ev(
                "f1",
                EventType.PAYMENT_FAILED,
                at(10),
                "t_c",
                method="card",
                amount_cents=1000,
                reason="declined",
            ),
        ]
        rows = {row.method: row for row in by_method(events)}
        self.assertEqual(set(rows), {"card", "cash"})

        card = rows["card"]
        self.assertEqual(card.captured, 1)
        self.assertEqual(card.failed, 1)
        self.assertEqual(card.attempts, 2)
        self.assertEqual(card.captured_cents, 4000)
        self.assertEqual(card.failed_cents, 1000)
        self.assertAlmostEqual(card.fail_rate, 0.5)

        cash = rows["cash"]
        self.assertEqual(cash.captured, 1)
        self.assertEqual(cash.failed, 0)
        self.assertEqual(cash.attempts, 1)
        self.assertEqual(cash.captured_cents, 8000)
        self.assertEqual(cash.failed_cents, 0)
        self.assertAlmostEqual(cash.fail_rate, 0.0)

    def test_empty_log_is_empty_list(self) -> None:
        self.assertEqual(by_method([]), [])

    def test_ignores_non_payment_events(self) -> None:
        events = [
            ev("e1", EventType.TICKET_OPENED, at(8), "t1", bay="1", vehicle="x"),
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
        self.assertEqual(by_method(events), [])

    def test_sorts_by_captured_cents_then_method(self) -> None:
        events = [
            ev(
                "e1",
                EventType.PAYMENT_CAPTURED,
                at(8),
                "t1",
                method="zzz",
                amount_cents=1000,
            ),
            ev(
                "e2",
                EventType.PAYMENT_CAPTURED,
                at(8, 1),
                "t2",
                method="aaa",
                amount_cents=1000,
            ),
            ev(
                "e3",
                EventType.PAYMENT_CAPTURED,
                at(8, 2),
                "t3",
                method="mid",
                amount_cents=5000,
            ),
        ]
        self.assertEqual([row.method for row in by_method(events)], ["mid", "aaa", "zzz"])

    def test_seeded_day_has_card_and_cash(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        rows = by_method(events)
        methods = {row.method for row in rows}
        self.assertIn("card", methods)
        self.assertIn("cash", methods)
        captured = sum(
            1 for e in events if e.type is EventType.PAYMENT_CAPTURED
        )
        failed = sum(1 for e in events if e.type is EventType.PAYMENT_FAILED)
        self.assertEqual(sum(row.captured for row in rows), captured)
        self.assertEqual(sum(row.failed for row in rows), failed)
        expected_rev = sum(
            int(e.payload.get("amount_cents", 0))
            for e in events
            if e.type is EventType.PAYMENT_CAPTURED
        )
        self.assertEqual(sum(row.captured_cents for row in rows), expected_rev)
        self.assertGreater(expected_rev, 0)
        card = next(row for row in rows if row.method == "card")
        self.assertGreater(card.failed, 0)
        cash = next(row for row in rows if row.method == "cash")
        self.assertEqual(cash.failed, 0)


class PayRenderTests(unittest.TestCase):
    def test_human_and_json_match_fold(self) -> None:
        events = [
            ev(
                "e1",
                EventType.PAYMENT_CAPTURED,
                at(8),
                "t1",
                method="card",
                amount_cents=6999,
            ),
            ev(
                "e2",
                EventType.PAYMENT_FAILED,
                at(8, 1),
                "t2",
                method="card",
                amount_cents=1299,
                reason="declined",
            ),
            ev(
                "e3",
                EventType.PAYMENT_CAPTURED,
                at(8, 2),
                "t3",
                method="cash",
                amount_cents=2199,
            ),
        ]
        rows = by_method(events)
        text = render_pay(events, rows)
        self.assertIn("pay", text)
        self.assertIn("2 methods", text)
        self.assertIn("card", text)
        self.assertIn("cash", text)
        self.assertIn("fail", text)
        payload = json.loads(render_pay_json(events, rows))
        self.assertEqual(payload["events"], len(events))
        self.assertEqual(
            [(r["method"], r["captured"], r["captured_cents"]) for r in payload["methods"]],
            [(row.method, row.captured, row.captured_cents) for row in rows],
        )
        card = next(r for r in payload["methods"] if r["method"] == "card")
        self.assertEqual(card["failed"], 1)
        self.assertAlmostEqual(card["fail_rate"], 0.5)
