from __future__ import annotations

import json
import unittest
from datetime import timedelta

from event_spine.bay import by_bay
from event_spine.events import EventType
from event_spine.report import render_bay, render_bay_json
from event_spine.simulate import SimConfig, simulate_day
from tests.helpers import at, ev, ticket_flow


class BayFoldTests(unittest.TestCase):
    def test_groups_by_ticket_opened_bay(self) -> None:
        events = [
            *ticket_flow(
                "t_a", at(8), 4000, prefix="a", bay="2", dwell=timedelta(minutes=30)
            ),
            *ticket_flow(
                "t_b", at(9), 8000, prefix="b", bay="1", dwell=timedelta(minutes=60)
            ),
            ev("o1", EventType.TICKET_OPENED, at(10), "t_open", bay="2", vehicle="x"),
        ]
        rows = {row.bay: row for row in by_bay(events)}
        self.assertEqual(set(rows), {"1", "2"})

        one = rows["1"]
        self.assertEqual(one.tickets, 1)
        self.assertEqual(one.closed, 1)
        self.assertEqual(one.open, 0)
        self.assertEqual(one.revenue_cents, 8000)
        self.assertAlmostEqual(one.dwell_p50_min or 0.0, 60.0)

        two = rows["2"]
        self.assertEqual(two.tickets, 2)
        self.assertEqual(two.closed, 1)
        self.assertEqual(two.open, 1)
        self.assertEqual(two.revenue_cents, 4000)
        self.assertAlmostEqual(two.dwell_p50_min or 0.0, 30.0)

    def test_open_ticket_skips_revenue_and_dwell(self) -> None:
        events = [
            ev("e1", EventType.TICKET_OPENED, at(8), "t1", bay="3", vehicle="x"),
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
        rows = by_bay(events)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].bay, "3")
        self.assertEqual(rows[0].tickets, 1)
        self.assertEqual(rows[0].closed, 0)
        self.assertEqual(rows[0].open, 1)
        self.assertEqual(rows[0].revenue_cents, 0)
        self.assertIsNone(rows[0].dwell_p50_min)

    def test_empty_log_is_empty_list(self) -> None:
        self.assertEqual(by_bay([]), [])

    def test_sorts_by_revenue_then_bay(self) -> None:
        events = [
            *ticket_flow("t_z", at(8), 1000, prefix="z", bay="Z"),
            *ticket_flow("t_a", at(9), 1000, prefix="a", bay="A"),
            *ticket_flow("t_m", at(10), 5000, prefix="m", bay="M"),
        ]
        self.assertEqual([row.bay for row in by_bay(events)], ["M", "A", "Z"])

    def test_seeded_day_has_three_bays_and_closed_revenue(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        rows = by_bay(events)
        self.assertEqual({row.bay for row in rows}, {"1", "2", "3"})
        from event_spine.project import project

        tickets = project(events)
        self.assertEqual(sum(row.tickets for row in rows), len(tickets))
        # Closed revenue matches the ticket projection, not captured amount_cents.
        expected = sum(t.total_cents for t in tickets.values() if t.closed)
        self.assertEqual(sum(row.revenue_cents for row in rows), expected)
        self.assertGreater(expected, 0)
        leftover = sum(1 for t in tickets.values() if not t.closed)
        self.assertEqual(sum(row.open for row in rows), leftover)


class BayRenderTests(unittest.TestCase):
    def test_human_and_json_match_fold(self) -> None:
        events = [
            *ticket_flow("t_a", at(8), 6999, prefix="a", bay="2"),
            *ticket_flow("t_b", at(9), 1299, prefix="b", bay="1"),
        ]
        rows = by_bay(events)
        text = render_bay(events, rows)
        self.assertIn("bay", text)
        self.assertIn("2 bays", text)
        self.assertIn("2 tickets", text)
        self.assertIn("bay 2", text)
        self.assertIn("bay 1", text)
        payload = json.loads(render_bay_json(events, rows))
        self.assertEqual(payload["events"], len(events))
        self.assertEqual(
            [(r["bay"], r["tickets"], r["revenue_cents"]) for r in payload["bays"]],
            [(row.bay, row.tickets, row.revenue_cents) for row in rows],
        )
