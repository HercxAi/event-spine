from __future__ import annotations

import json
import unittest
from datetime import timedelta

from event_spine.dwell import by_dwell
from event_spine.events import EventType
from event_spine.report import render_dwell, render_dwell_json
from event_spine.simulate import SimConfig, simulate_day
from tests.helpers import at, ev, ticket_flow


class DwellFoldTests(unittest.TestCase):
    def test_buckets_closed_tickets(self) -> None:
        events = [
            *ticket_flow(
                "t_fast", at(8), 4000, prefix="f", dwell=timedelta(minutes=3)
            ),
            *ticket_flow(
                "t_mid", at(9), 5000, prefix="m", dwell=timedelta(minutes=10)
            ),
            *ticket_flow(
                "t_slow", at(10), 8000, prefix="s", dwell=timedelta(minutes=90)
            ),
            ev("o1", EventType.TICKET_OPENED, at(11), "t_open", bay="1", vehicle="x"),
        ]
        rows = {row.bucket: row for row in by_dwell(events)}
        self.assertEqual(set(rows), {"<5", "5-15", "15-60", "60+"})

        self.assertEqual(rows["<5"].tickets, 1)
        self.assertEqual(rows["<5"].revenue_cents, 4000)
        self.assertAlmostEqual(rows["<5"].dwell_p50_min or 0.0, 3.0)

        self.assertEqual(rows["5-15"].tickets, 1)
        self.assertEqual(rows["5-15"].revenue_cents, 5000)
        self.assertAlmostEqual(rows["5-15"].dwell_p50_min or 0.0, 10.0)

        self.assertEqual(rows["15-60"].tickets, 0)
        self.assertEqual(rows["15-60"].revenue_cents, 0)
        self.assertIsNone(rows["15-60"].dwell_p50_min)

        self.assertEqual(rows["60+"].tickets, 1)
        self.assertEqual(rows["60+"].revenue_cents, 8000)
        self.assertAlmostEqual(rows["60+"].dwell_p50_min or 0.0, 90.0)

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
        rows = by_dwell(events)
        self.assertEqual([row.bucket for row in rows], ["<5", "5-15", "15-60", "60+"])
        self.assertEqual(sum(row.tickets for row in rows), 0)
        self.assertEqual(sum(row.revenue_cents for row in rows), 0)

    def test_empty_log_emits_empty_bands(self) -> None:
        rows = by_dwell([])
        self.assertEqual([row.bucket for row in rows], ["<5", "5-15", "15-60", "60+"])
        self.assertEqual(sum(row.tickets for row in rows), 0)

    def test_fixed_bucket_order(self) -> None:
        events = [
            *ticket_flow(
                "t_slow", at(8), 1000, prefix="s", dwell=timedelta(minutes=120)
            ),
            *ticket_flow(
                "t_fast", at(9), 1000, prefix="f", dwell=timedelta(minutes=2)
            ),
        ]
        self.assertEqual(
            [row.bucket for row in by_dwell(events)],
            ["<5", "5-15", "15-60", "60+"],
        )

    def test_seeded_day_puts_plant_in_60_plus(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        rows = {row.bucket: row for row in by_dwell(events)}
        from event_spine.project import project

        closed = [t for t in project(events).values() if t.closed]
        self.assertEqual(sum(row.tickets for row in rows.values()), len(closed))
        expected = sum(t.total_cents for t in closed)
        self.assertEqual(sum(row.revenue_cents for row in rows.values()), expected)
        self.assertGreater(rows["<5"].tickets, 0)
        self.assertEqual(rows["60+"].tickets, 1)
        self.assertGreater(rows["60+"].dwell_p50_min or 0.0, 60.0)


class DwellRenderTests(unittest.TestCase):
    def test_human_and_json_match_fold(self) -> None:
        events = [
            *ticket_flow(
                "t_a", at(8), 6999, prefix="a", dwell=timedelta(minutes=4)
            ),
            *ticket_flow(
                "t_b", at(9), 1299, prefix="b", dwell=timedelta(minutes=75)
            ),
        ]
        rows = by_dwell(events)
        text = render_dwell(events, rows)
        self.assertIn("dwell", text)
        self.assertIn("4 bands", text)
        self.assertIn("2 closed", text)
        self.assertIn("<5", text)
        self.assertIn("60+", text)
        payload = json.loads(render_dwell_json(events, rows))
        self.assertEqual(payload["events"], len(events))
        self.assertEqual(
            [(r["bucket"], r["tickets"], r["revenue_cents"]) for r in payload["buckets"]],
            [(row.bucket, row.tickets, row.revenue_cents) for row in rows],
        )
