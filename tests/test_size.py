from __future__ import annotations

import json
import unittest

from event_spine.events import EventType
from event_spine.project import project
from event_spine.report import render_size, render_size_json
from event_spine.simulate import SimConfig, simulate_day
from event_spine.size import by_size
from tests.helpers import at, ev, ticket_flow


class SizeFoldTests(unittest.TestCase):
    def test_buckets_closed_tickets(self) -> None:
        events = [
            *ticket_flow("t_small", at(8), 4000, prefix="s"),
            *ticket_flow("t_mid", at(9), 7500, prefix="m"),
            *ticket_flow("t_big", at(10), 15000, prefix="b"),
            *ticket_flow("t_whale", at(11), 56500, prefix="w"),
            ev("o1", EventType.TICKET_OPENED, at(12), "t_open", bay="1", vehicle="x"),
        ]
        rows = {row.bucket: row for row in by_size(events)}
        self.assertEqual(set(rows), {"<$50", "$50-100", "$100-200", "$200+"})

        self.assertEqual(rows["<$50"].tickets, 1)
        self.assertEqual(rows["<$50"].revenue_cents, 4000)
        self.assertEqual(rows["<$50"].total_p50_cents, 4000)

        self.assertEqual(rows["$50-100"].tickets, 1)
        self.assertEqual(rows["$50-100"].revenue_cents, 7500)
        self.assertEqual(rows["$50-100"].total_p50_cents, 7500)

        self.assertEqual(rows["$100-200"].tickets, 1)
        self.assertEqual(rows["$100-200"].revenue_cents, 15000)

        self.assertEqual(rows["$200+"].tickets, 1)
        self.assertEqual(rows["$200+"].revenue_cents, 56500)
        self.assertEqual(rows["$200+"].total_p50_cents, 56500)

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
        rows = by_size(events)
        self.assertEqual(
            [row.bucket for row in rows],
            ["<$50", "$50-100", "$100-200", "$200+"],
        )
        self.assertEqual(sum(row.tickets for row in rows), 0)
        self.assertEqual(sum(row.revenue_cents for row in rows), 0)

    def test_empty_log_emits_empty_bands(self) -> None:
        rows = by_size([])
        self.assertEqual(
            [row.bucket for row in rows],
            ["<$50", "$50-100", "$100-200", "$200+"],
        )
        self.assertEqual(sum(row.tickets for row in rows), 0)

    def test_fixed_bucket_order(self) -> None:
        events = [
            *ticket_flow("t_whale", at(8), 56500, prefix="w"),
            *ticket_flow("t_small", at(9), 4000, prefix="s"),
        ]
        self.assertEqual(
            [row.bucket for row in by_size(events)],
            ["<$50", "$50-100", "$100-200", "$200+"],
        )

    def test_seeded_day_puts_whale_in_200_plus(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        rows = {row.bucket: row for row in by_size(events)}
        closed = [t for t in project(events).values() if t.closed]
        self.assertEqual(sum(row.tickets for row in rows.values()), len(closed))
        expected = sum(t.total_cents for t in closed)
        self.assertEqual(sum(row.revenue_cents for row in rows.values()), expected)
        self.assertEqual(rows["<$50"].tickets, 0)
        self.assertGreater(rows["$50-100"].tickets, 0)
        self.assertEqual(rows["$200+"].tickets, 1)
        self.assertEqual(rows["$200+"].total_p50_cents, 56498)


class SizeRenderTests(unittest.TestCase):
    def test_human_and_json_match_fold(self) -> None:
        events = [
            *ticket_flow("t_a", at(8), 6999, prefix="a"),
            *ticket_flow("t_b", at(9), 56500, prefix="b"),
        ]
        rows = by_size(events)
        text = render_size(events, rows)
        self.assertIn("size", text)
        self.assertIn("4 bands", text)
        self.assertIn("2 closed", text)
        self.assertIn("$50-100", text)
        self.assertIn("$200+", text)
        payload = json.loads(render_size_json(events, rows))
        self.assertEqual(payload["events"], len(events))
        self.assertEqual(
            [(r["bucket"], r["tickets"], r["revenue_cents"]) for r in payload["buckets"]],
            [(row.bucket, row.tickets, row.revenue_cents) for row in rows],
        )
