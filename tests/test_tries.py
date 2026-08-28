from __future__ import annotations

import json
import unittest

from event_spine.events import EventType
from event_spine.project import project
from event_spine.report import render_tries, render_tries_json
from event_spine.simulate import SimConfig, simulate_day
from event_spine.tries import by_tries
from tests.helpers import at, ev, ticket_flow


class TriesFoldTests(unittest.TestCase):
    def test_buckets_closed_tickets(self) -> None:
        events = [
            *ticket_flow("t1", at(8), 4000, prefix="a"),
            *ticket_flow("t2", at(9), 7500, prefix="b", fail=True),
            *ticket_flow("t3", at(10), 12000, prefix="c", fail=True),
            *ticket_flow("t4", at(11), 20000, prefix="d"),
            ev("o1", EventType.TICKET_OPENED, at(13), "t_open", bay="1", vehicle="x"),
        ]
        # fail=True does fail then capture → 2 attempts
        # add a 3-attempt ticket manually
        t = at(12)
        triple = [
            ev("x01", EventType.TICKET_OPENED, t, "t5", bay="2", vehicle="y"),
            ev(
                "x02",
                EventType.LINE_ITEM_ADDED,
                at(12, 1),
                "t5",
                sku="OIL-CONV",
                description="oil",
                qty=1,
                unit_cents=5000,
            ),
            ev(
                "x03",
                EventType.PAYMENT_FAILED,
                at(12, 2),
                "t5",
                method="card",
                amount_cents=5000,
                reason="network",
            ),
            ev(
                "x04",
                EventType.PAYMENT_FAILED,
                at(12, 3),
                "t5",
                method="card",
                amount_cents=5000,
                reason="network",
            ),
            ev(
                "x05",
                EventType.PAYMENT_CAPTURED,
                at(12, 4),
                "t5",
                method="card",
                amount_cents=5000,
            ),
            ev("x06", EventType.TICKET_CLOSED, at(12, 5), "t5", total_cents=5000),
        ]
        events = [
            *ticket_flow("t1", at(8), 4000, prefix="a"),
            *ticket_flow("t2", at(9), 7500, prefix="b", fail=True),
            *ticket_flow("t3", at(10), 12000, prefix="c", fail=True),
            *ticket_flow("t4", at(11), 20000, prefix="d"),
            *triple,
            ev("o1", EventType.TICKET_OPENED, at(13), "t_open", bay="1", vehicle="x"),
        ]
        rows = {row.bucket: row for row in by_tries(events)}
        self.assertEqual(set(rows), {"1", "2", "3+"})

        self.assertEqual(rows["1"].tickets, 2)
        self.assertEqual(rows["1"].revenue_cents, 24000)
        self.assertEqual(rows["1"].total_p50_cents, 12000)

        self.assertEqual(rows["2"].tickets, 2)
        self.assertEqual(rows["2"].revenue_cents, 19500)

        self.assertEqual(rows["3+"].tickets, 1)
        self.assertEqual(rows["3+"].revenue_cents, 5000)
        self.assertEqual(rows["3+"].total_p50_cents, 5000)

    def test_open_ticket_ignored(self) -> None:
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
        rows = by_tries(events)
        self.assertEqual([row.bucket for row in rows], ["1", "2", "3+"])
        self.assertEqual(sum(row.tickets for row in rows), 0)
        self.assertEqual(sum(row.revenue_cents for row in rows), 0)

    def test_empty_log_emits_empty_bands(self) -> None:
        rows = by_tries([])
        self.assertEqual([row.bucket for row in rows], ["1", "2", "3+"])
        self.assertEqual(sum(row.tickets for row in rows), 0)

    def test_fixed_bucket_order(self) -> None:
        events = [
            *ticket_flow("t_one", at(8), 4000, prefix="s"),
            *ticket_flow("t_two", at(9), 5000, prefix="f", fail=True),
        ]
        self.assertEqual(
            [row.bucket for row in by_tries(events)],
            ["1", "2", "3+"],
        )

    def test_seeded_day_puts_terminal_sulk_in_3_plus(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        rows = {row.bucket: row for row in by_tries(events)}
        closed = [t for t in project(events).values() if t.closed]
        self.assertEqual(sum(row.tickets for row in rows.values()), len(closed))
        expected = sum(t.total_cents for t in closed)
        self.assertEqual(sum(row.revenue_cents for row in rows.values()), expected)
        self.assertGreater(rows["1"].tickets, 0)
        self.assertEqual(rows["2"].tickets, 0)
        self.assertEqual(rows["3+"].tickets, 6)
        deepest = max(len(t.payments) for t in closed)
        self.assertGreaterEqual(deepest, 3)


class TriesRenderTests(unittest.TestCase):
    def test_human_and_json_match_fold(self) -> None:
        events = [
            *ticket_flow("t_a", at(8), 6999, prefix="a"),
            *ticket_flow("t_b", at(9), 8000, prefix="b", fail=True),
        ]
        rows = by_tries(events)
        text = render_tries(events, rows)
        self.assertIn("tries", text)
        self.assertIn("3 bands", text)
        self.assertIn("2 closed", text)
        self.assertIn("1", text)
        self.assertIn("2", text)
        payload = json.loads(render_tries_json(events, rows))
        self.assertEqual(payload["events"], len(events))
        self.assertEqual(
            [(r["bucket"], r["tickets"], r["revenue_cents"]) for r in payload["buckets"]],
            [(row.bucket, row.tickets, row.revenue_cents) for row in rows],
        )
