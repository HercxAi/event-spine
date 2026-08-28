from __future__ import annotations

import json
import unittest

from event_spine.events import EventType
from event_spine.lines import by_lines
from event_spine.project import project
from event_spine.report import render_lines, render_lines_json
from event_spine.simulate import SimConfig, simulate_day
from tests.helpers import at, ev, ticket_flow


class LinesFoldTests(unittest.TestCase):
    def test_buckets_closed_tickets(self) -> None:
        events = [
            *ticket_flow("t1", at(8), 4000, prefix="a", items=[("OIL-CONV", 4000)]),
            *ticket_flow(
                "t2",
                at(9),
                7500,
                prefix="b",
                items=[("OIL-CONV", 4500), ("FIL-OIL", 3000)],
            ),
            *ticket_flow(
                "t3",
                at(10),
                12000,
                prefix="c",
                items=[("OIL-SYN", 6000), ("FIL-OIL", 3000), ("WIPER", 3000)],
            ),
            *ticket_flow(
                "t4",
                at(11),
                20000,
                prefix="d",
                items=[
                    ("OIL-SYN", 6000),
                    ("FIL-OIL", 3000),
                    ("WIPER", 3000),
                    ("AIR-FIL", 8000),
                ],
            ),
            *ticket_flow(
                "t5",
                at(12),
                30000,
                prefix="e",
                items=[
                    ("OIL-SYN", 6000),
                    ("FIL-OIL", 3000),
                    ("WIPER", 3000),
                    ("AIR-FIL", 8000),
                    ("TRN-FLUSH", 10000),
                ],
            ),
            ev("o1", EventType.TICKET_OPENED, at(13), "t_open", bay="1", vehicle="x"),
        ]
        rows = {row.bucket: row for row in by_lines(events)}
        self.assertEqual(set(rows), {"1", "2", "3", "4+"})

        self.assertEqual(rows["1"].tickets, 1)
        self.assertEqual(rows["1"].revenue_cents, 4000)
        self.assertEqual(rows["1"].total_p50_cents, 4000)

        self.assertEqual(rows["2"].tickets, 1)
        self.assertEqual(rows["2"].revenue_cents, 7500)

        self.assertEqual(rows["3"].tickets, 1)
        self.assertEqual(rows["3"].revenue_cents, 12000)

        self.assertEqual(rows["4+"].tickets, 2)
        self.assertEqual(rows["4+"].revenue_cents, 50000)
        self.assertEqual(rows["4+"].total_p50_cents, 25000)

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
        rows = by_lines(events)
        self.assertEqual([row.bucket for row in rows], ["1", "2", "3", "4+"])
        self.assertEqual(sum(row.tickets for row in rows), 0)
        self.assertEqual(sum(row.revenue_cents for row in rows), 0)

    def test_empty_log_emits_empty_bands(self) -> None:
        rows = by_lines([])
        self.assertEqual([row.bucket for row in rows], ["1", "2", "3", "4+"])
        self.assertEqual(sum(row.tickets for row in rows), 0)

    def test_fixed_bucket_order(self) -> None:
        events = [
            *ticket_flow(
                "t_deep",
                at(8),
                20000,
                prefix="d",
                items=[
                    ("A", 5000),
                    ("B", 5000),
                    ("C", 5000),
                    ("D", 5000),
                ],
            ),
            *ticket_flow("t_one", at(9), 4000, prefix="s"),
        ]
        self.assertEqual(
            [row.bucket for row in by_lines(events)],
            ["1", "2", "3", "4+"],
        )

    def test_seeded_day_puts_whale_in_4_plus(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        rows = {row.bucket: row for row in by_lines(events)}
        closed = [t for t in project(events).values() if t.closed]
        self.assertEqual(sum(row.tickets for row in rows.values()), len(closed))
        expected = sum(t.total_cents for t in closed)
        self.assertEqual(sum(row.revenue_cents for row in rows.values()), expected)
        self.assertEqual(rows["1"].tickets, 0)
        self.assertGreater(rows["3"].tickets, 0)
        self.assertGreater(rows["4+"].tickets, 0)
        deepest = max(len(t.items) for t in closed)
        self.assertGreaterEqual(deepest, 4)


class LinesRenderTests(unittest.TestCase):
    def test_human_and_json_match_fold(self) -> None:
        events = [
            *ticket_flow("t_a", at(8), 6999, prefix="a"),
            *ticket_flow(
                "t_b",
                at(9),
                20000,
                prefix="b",
                items=[
                    ("A", 5000),
                    ("B", 5000),
                    ("C", 5000),
                    ("D", 5000),
                ],
            ),
        ]
        rows = by_lines(events)
        text = render_lines(events, rows)
        self.assertIn("lines", text)
        self.assertIn("4 bands", text)
        self.assertIn("2 closed", text)
        self.assertIn("1", text)
        self.assertIn("4+", text)
        payload = json.loads(render_lines_json(events, rows))
        self.assertEqual(payload["events"], len(events))
        self.assertEqual(
            [(r["bucket"], r["tickets"], r["revenue_cents"]) for r in payload["buckets"]],
            [(row.bucket, row.tickets, row.revenue_cents) for row in rows],
        )
