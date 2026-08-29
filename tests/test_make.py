from __future__ import annotations

import json
import unittest
from datetime import timedelta

from event_spine.events import EventType
from event_spine.make import by_make, make_of
from event_spine.project import project
from event_spine.report import render_make, render_make_json
from event_spine.simulate import SimConfig, simulate_day
from tests.helpers import at, ev, ticket_flow


class MakeOfTests(unittest.TestCase):
    def test_skips_leading_year(self) -> None:
        self.assertEqual(make_of("2018 Honda Civic"), "Honda")
        self.assertEqual(make_of("2015 Ford F-150"), "Ford")
        self.assertEqual(make_of("2017 BMW 328i"), "BMW")
        self.assertEqual(make_of("2014 Jeep Grand Cherokee"), "Jeep")

    def test_bare_name_keeps_first_word(self) -> None:
        self.assertEqual(make_of("Zebra"), "Zebra")
        self.assertEqual(make_of("Chevy Silverado"), "Chevy")

    def test_empty_stays_empty(self) -> None:
        self.assertEqual(make_of(""), "")
        self.assertEqual(make_of("   "), "")


class MakeFoldTests(unittest.TestCase):
    def test_groups_by_parsed_make(self) -> None:
        events = [
            *ticket_flow(
                "t_a",
                at(8),
                4000,
                prefix="a",
                vehicle="2018 Honda Civic",
                dwell=timedelta(minutes=30),
            ),
            *ticket_flow(
                "t_b",
                at(9),
                8000,
                prefix="b",
                vehicle="2015 Ford F-150",
                dwell=timedelta(minutes=60),
            ),
            ev(
                "o1",
                EventType.TICKET_OPENED,
                at(10),
                "t_open",
                bay="1",
                vehicle="2021 Honda CR-V",
            ),
        ]
        rows = {row.make: row for row in by_make(events)}
        self.assertEqual(set(rows), {"Honda", "Ford"})

        honda = rows["Honda"]
        self.assertEqual(honda.tickets, 2)
        self.assertEqual(honda.closed, 1)
        self.assertEqual(honda.open, 1)
        self.assertEqual(honda.revenue_cents, 4000)
        self.assertAlmostEqual(honda.dwell_p50_min or 0.0, 30.0)

        ford = rows["Ford"]
        self.assertEqual(ford.tickets, 1)
        self.assertEqual(ford.closed, 1)
        self.assertEqual(ford.open, 0)
        self.assertEqual(ford.revenue_cents, 8000)
        self.assertAlmostEqual(ford.dwell_p50_min or 0.0, 60.0)

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
        rows = by_make(events)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].make, "Toyota")
        self.assertEqual(rows[0].tickets, 1)
        self.assertEqual(rows[0].closed, 0)
        self.assertEqual(rows[0].open, 1)
        self.assertEqual(rows[0].revenue_cents, 0)
        self.assertIsNone(rows[0].dwell_p50_min)

    def test_empty_log_is_empty_list(self) -> None:
        self.assertEqual(by_make([]), [])

    def test_sorts_by_revenue_then_make(self) -> None:
        events = [
            *ticket_flow("t_z", at(8), 1000, prefix="z", vehicle="Zebra"),
            *ticket_flow("t_a", at(9), 1000, prefix="a", vehicle="Alpha"),
            *ticket_flow("t_m", at(10), 5000, prefix="m", vehicle="Mike"),
        ]
        self.assertEqual(
            [row.make for row in by_make(events)],
            ["Mike", "Alpha", "Zebra"],
        )

    def test_seeded_day_covers_all_tickets(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        rows = by_make(events)
        tickets = project(events)
        self.assertEqual(sum(row.tickets for row in rows), len(tickets))
        expected = sum(t.total_cents for t in tickets.values() if t.closed)
        self.assertEqual(sum(row.revenue_cents for row in rows), expected)
        self.assertGreater(expected, 0)
        leftover = sum(1 for t in tickets.values() if not t.closed)
        self.assertEqual(sum(row.open for row in rows), leftover)
        self.assertGreaterEqual(len(rows), 2)
        names = {row.make for row in rows}
        self.assertIn("Honda", names)
        self.assertIn("Ford", names)
        self.assertNotIn("", names)


class MakeRenderTests(unittest.TestCase):
    def test_human_and_json_match_fold(self) -> None:
        events = [
            *ticket_flow(
                "t_a", at(8), 6999, prefix="a", vehicle="2018 Honda Civic"
            ),
            *ticket_flow(
                "t_b", at(9), 1299, prefix="b", vehicle="2015 Ford F-150"
            ),
        ]
        rows = by_make(events)
        text = render_make(events, rows)
        self.assertIn("make", text)
        self.assertIn("2 makes", text)
        self.assertIn("2 tickets", text)
        self.assertIn("Honda", text)
        self.assertIn("Ford", text)
        payload = json.loads(render_make_json(events, rows))
        self.assertEqual(payload["events"], len(events))
        self.assertEqual(
            [(r["make"], r["tickets"], r["revenue_cents"]) for r in payload["makes"]],
            [(row.make, row.tickets, row.revenue_cents) for row in rows],
        )
