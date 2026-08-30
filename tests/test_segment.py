from __future__ import annotations

import json
import unittest
from datetime import timedelta

from event_spine.events import EventType
from event_spine.project import project
from event_spine.report import render_segment, render_segment_json
from event_spine.segment import by_segment, segment_of
from event_spine.simulate import SimConfig, simulate_day
from tests.helpers import at, ev, ticket_flow


class SegmentOfTests(unittest.TestCase):
    def test_classifies_seeded_vehicles(self) -> None:
        self.assertEqual(segment_of("2018 Honda Civic"), "car")
        self.assertEqual(segment_of("2011 Toyota Camry"), "car")
        self.assertEqual(segment_of("2017 BMW 328i"), "luxury")
        self.assertEqual(segment_of("2021 Toyota RAV4"), "suv")
        self.assertEqual(segment_of("2013 Honda CR-V"), "suv")
        self.assertEqual(segment_of("2019 Subaru Outback"), "suv")
        self.assertEqual(segment_of("2016 Mazda CX-5"), "suv")
        self.assertEqual(segment_of("2020 Hyundai Tucson"), "suv")
        self.assertEqual(segment_of("2014 Jeep Grand Cherokee"), "suv")
        self.assertEqual(segment_of("2022 Ford Escape"), "suv")
        self.assertEqual(segment_of("2015 Ford F-150"), "truck")
        self.assertEqual(segment_of("2012 Chevy Silverado"), "truck")

    def test_luxury_makes_and_land_rover(self) -> None:
        self.assertEqual(segment_of("2020 Tesla Model 3"), "luxury")
        self.assertEqual(segment_of("2019 Lexus RX"), "luxury")
        self.assertEqual(segment_of("2018 Land Rover Discovery"), "luxury")
        self.assertEqual(segment_of("2021 Mercedes C300"), "luxury")

    def test_truck_beats_luxury(self) -> None:
        self.assertEqual(segment_of("2024 Tesla Cybertruck"), "truck")
        self.assertEqual(segment_of("2020 Jeep Gladiator"), "truck")

    def test_bare_name_still_classifies(self) -> None:
        self.assertEqual(segment_of("Chevy Silverado"), "truck")
        self.assertEqual(segment_of("Honda Civic"), "car")
        self.assertEqual(segment_of("BMW 328i"), "luxury")

    def test_empty_and_make_only_stay_empty(self) -> None:
        self.assertEqual(segment_of(""), "")
        self.assertEqual(segment_of("   "), "")
        self.assertEqual(segment_of("Zebra"), "")


class SegmentFoldTests(unittest.TestCase):
    def test_groups_by_classified_segment(self) -> None:
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
                vehicle="2017 BMW 328i",
            ),
        ]
        rows = {row.segment: row for row in by_segment(events)}
        self.assertEqual(set(rows), {"car", "truck", "luxury"})

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

        luxury = rows["luxury"]
        self.assertEqual(luxury.tickets, 1)
        self.assertEqual(luxury.closed, 0)
        self.assertEqual(luxury.open, 1)
        self.assertEqual(luxury.revenue_cents, 0)
        self.assertIsNone(luxury.dwell_p50_min)

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
        rows = by_segment(events)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].segment, "suv")
        self.assertEqual(rows[0].tickets, 1)
        self.assertEqual(rows[0].closed, 0)
        self.assertEqual(rows[0].open, 1)
        self.assertEqual(rows[0].revenue_cents, 0)
        self.assertIsNone(rows[0].dwell_p50_min)

    def test_empty_log_is_empty_list(self) -> None:
        self.assertEqual(by_segment([]), [])

    def test_sorts_by_revenue_then_segment(self) -> None:
        events = [
            *ticket_flow("t_z", at(8), 1000, prefix="z", vehicle="2018 Honda Civic"),
            *ticket_flow("t_a", at(9), 1000, prefix="a", vehicle="2011 Toyota Camry"),
            *ticket_flow("t_m", at(10), 5000, prefix="m", vehicle="2015 Ford F-150"),
        ]
        self.assertEqual([row.segment for row in by_segment(events)], ["truck", "car"])
        self.assertEqual(by_segment(events)[1].tickets, 2)

    def test_tie_breaks_alphabetically(self) -> None:
        events = [
            *ticket_flow("t_c", at(8), 2000, prefix="c", vehicle="2018 Honda Civic"),
            *ticket_flow("t_l", at(9), 2000, prefix="l", vehicle="2017 BMW 328i"),
        ]
        self.assertEqual([row.segment for row in by_segment(events)], ["car", "luxury"])

    def test_seeded_day_covers_all_tickets(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        rows = by_segment(events)
        tickets = project(events)
        self.assertEqual(sum(row.tickets for row in rows), len(tickets))
        expected = sum(t.total_cents for t in tickets.values() if t.closed)
        self.assertEqual(sum(row.revenue_cents for row in rows), expected)
        self.assertGreater(expected, 0)
        leftover = sum(1 for t in tickets.values() if not t.closed)
        self.assertEqual(sum(row.open for row in rows), leftover)
        names = {row.segment for row in rows}
        self.assertTrue({"car", "suv", "truck", "luxury"} <= names)
        self.assertNotIn("", names)


class SegmentRenderTests(unittest.TestCase):
    def test_human_and_json_match_fold(self) -> None:
        events = [
            *ticket_flow(
                "t_a", at(8), 6999, prefix="a", vehicle="2018 Honda Civic"
            ),
            *ticket_flow(
                "t_b", at(9), 1299, prefix="b", vehicle="2015 Ford F-150"
            ),
        ]
        rows = by_segment(events)
        text = render_segment(events, rows)
        self.assertIn("segment", text)
        self.assertIn("2 segments", text)
        self.assertIn("2 tickets", text)
        self.assertIn("car", text)
        self.assertIn("truck", text)
        payload = json.loads(render_segment_json(events, rows))
        self.assertEqual(payload["events"], len(events))
        self.assertEqual(
            [(r["segment"], r["tickets"], r["revenue_cents"]) for r in payload["segments"]],
            [(row.segment, row.tickets, row.revenue_cents) for row in rows],
        )
