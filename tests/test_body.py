from __future__ import annotations

import json
import unittest
from datetime import timedelta

from event_spine.body import by_body, body_of
from event_spine.events import EventType
from event_spine.project import project
from event_spine.report import render_body, render_body_json
from event_spine.simulate import SimConfig, simulate_day
from tests.helpers import at, ev, ticket_flow


class BodyOfTests(unittest.TestCase):
    def test_classifies_seeded_models(self) -> None:
        self.assertEqual(body_of("2018 Honda Civic"), "car")
        self.assertEqual(body_of("2011 Toyota Camry"), "car")
        self.assertEqual(body_of("2017 BMW 328i"), "car")
        self.assertEqual(body_of("2021 Toyota RAV4"), "SUV")
        self.assertEqual(body_of("2013 Honda CR-V"), "SUV")
        self.assertEqual(body_of("2019 Subaru Outback"), "SUV")
        self.assertEqual(body_of("2015 Ford F-150"), "truck")
        self.assertEqual(body_of("2012 Chevy Silverado"), "truck")

    def test_bare_name_still_classifies(self) -> None:
        self.assertEqual(body_of("Chevy Silverado"), "truck")
        self.assertEqual(body_of("Honda Civic"), "car")

    def test_empty_and_make_only_stay_empty(self) -> None:
        self.assertEqual(body_of(""), "")
        self.assertEqual(body_of("   "), "")
        self.assertEqual(body_of("Zebra"), "")


class BodyFoldTests(unittest.TestCase):
    def test_groups_by_classified_body(self) -> None:
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
        rows = {row.body: row for row in by_body(events)}
        self.assertEqual(set(rows), {"car", "truck", "SUV"})

        car = rows["car"]
        self.assertEqual(car.tickets, 1)
        self.assertEqual(car.closed, 1)
        self.assertEqual(car.open, 0)
        self.assertEqual(car.revenue_cents, 4000)
        self.assertAlmostEqual(car.dwell_p50_min or 0.0, 30.0)

        truck = rows["truck"]
        self.assertEqual(truck.tickets, 1)
        self.assertEqual(truck.closed, 1)
        self.assertEqual(truck.open, 0)
        self.assertEqual(truck.revenue_cents, 8000)
        self.assertAlmostEqual(truck.dwell_p50_min or 0.0, 60.0)

        suv = rows["SUV"]
        self.assertEqual(suv.tickets, 1)
        self.assertEqual(suv.closed, 0)
        self.assertEqual(suv.open, 1)
        self.assertEqual(suv.revenue_cents, 0)
        self.assertIsNone(suv.dwell_p50_min)

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
        rows = by_body(events)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].body, "SUV")
        self.assertEqual(rows[0].tickets, 1)
        self.assertEqual(rows[0].closed, 0)
        self.assertEqual(rows[0].open, 1)
        self.assertEqual(rows[0].revenue_cents, 0)
        self.assertIsNone(rows[0].dwell_p50_min)

    def test_empty_log_is_empty_list(self) -> None:
        self.assertEqual(by_body([]), [])

    def test_sorts_by_revenue_then_body(self) -> None:
        events = [
            *ticket_flow("t_z", at(8), 1000, prefix="z", vehicle="2018 Honda Civic"),
            *ticket_flow("t_a", at(9), 1000, prefix="a", vehicle="2017 BMW 328i"),
            *ticket_flow("t_m", at(10), 5000, prefix="m", vehicle="2015 Ford F-150"),
        ]
        self.assertEqual([row.body for row in by_body(events)], ["truck", "car"])
        self.assertEqual(by_body(events)[1].tickets, 2)

    def test_seeded_day_covers_all_tickets(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        rows = by_body(events)
        tickets = project(events)
        self.assertEqual(sum(row.tickets for row in rows), len(tickets))
        expected = sum(t.total_cents for t in tickets.values() if t.closed)
        self.assertEqual(sum(row.revenue_cents for row in rows), expected)
        self.assertGreater(expected, 0)
        leftover = sum(1 for t in tickets.values() if not t.closed)
        self.assertEqual(sum(row.open for row in rows), leftover)
        names = {row.body for row in rows}
        self.assertEqual(names, {"car", "SUV", "truck"})
        self.assertNotIn("", names)


class BodyRenderTests(unittest.TestCase):
    def test_human_and_json_match_fold(self) -> None:
        events = [
            *ticket_flow(
                "t_a", at(8), 6999, prefix="a", vehicle="2018 Honda Civic"
            ),
            *ticket_flow(
                "t_b", at(9), 1299, prefix="b", vehicle="2015 Ford F-150"
            ),
        ]
        rows = by_body(events)
        text = render_body(events, rows)
        self.assertIn("body", text)
        self.assertIn("2 bodies", text)
        self.assertIn("2 tickets", text)
        self.assertIn("car", text)
        self.assertIn("truck", text)
        payload = json.loads(render_body_json(events, rows))
        self.assertEqual(payload["events"], len(events))
        self.assertEqual(
            [(r["body"], r["tickets"], r["revenue_cents"]) for r in payload["bodies"]],
            [(row.body, row.tickets, row.revenue_cents) for row in rows],
        )
