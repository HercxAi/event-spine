from __future__ import annotations

import json
import unittest
from datetime import timedelta

from event_spine.events import EventType
from event_spine.origin import by_origin, origin_of
from event_spine.project import project
from event_spine.report import render_origin, render_origin_json
from event_spine.simulate import SimConfig, simulate_day
from tests.helpers import at, ev, ticket_flow


class OriginOfTests(unittest.TestCase):
    def test_classifies_seeded_makes(self) -> None:
        self.assertEqual(origin_of("2018 Honda Civic"), "Japan")
        self.assertEqual(origin_of("2021 Toyota RAV4"), "Japan")
        self.assertEqual(origin_of("2019 Subaru Outback"), "Japan")
        self.assertEqual(origin_of("2016 Mazda CX-5"), "Japan")
        self.assertEqual(origin_of("2015 Ford F-150"), "US")
        self.assertEqual(origin_of("2012 Chevy Silverado"), "US")
        self.assertEqual(origin_of("2014 Jeep Grand Cherokee"), "US")
        self.assertEqual(origin_of("2020 Hyundai Tucson"), "Korea")
        self.assertEqual(origin_of("2017 BMW 328i"), "Germany")

    def test_empty_and_unknown_stay_empty(self) -> None:
        self.assertEqual(origin_of(""), "")
        self.assertEqual(origin_of("   "), "")
        self.assertEqual(origin_of("2018 Ferrari 488"), "")


class OriginFoldTests(unittest.TestCase):
    def test_groups_by_classified_origin(self) -> None:
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
                vehicle="2020 Hyundai Tucson",
            ),
        ]
        rows = {row.origin: row for row in by_origin(events)}
        self.assertEqual(set(rows), {"Japan", "US", "Korea"})

        japan = rows["Japan"]
        self.assertEqual(japan.tickets, 1)
        self.assertEqual(japan.closed, 1)
        self.assertEqual(japan.open, 0)
        self.assertEqual(japan.revenue_cents, 4000)
        self.assertAlmostEqual(japan.dwell_p50_min or 0.0, 30.0)

        us = rows["US"]
        self.assertEqual(us.tickets, 1)
        self.assertEqual(us.closed, 1)
        self.assertEqual(us.open, 0)
        self.assertEqual(us.revenue_cents, 8000)
        self.assertAlmostEqual(us.dwell_p50_min or 0.0, 60.0)

        korea = rows["Korea"]
        self.assertEqual(korea.tickets, 1)
        self.assertEqual(korea.closed, 0)
        self.assertEqual(korea.open, 1)
        self.assertEqual(korea.revenue_cents, 0)
        self.assertIsNone(korea.dwell_p50_min)

    def test_open_ticket_skips_revenue_and_dwell(self) -> None:
        events = [
            ev(
                "e1",
                EventType.TICKET_OPENED,
                at(8),
                "t1",
                bay="3",
                vehicle="2017 BMW 328i",
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
        rows = by_origin(events)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].origin, "Germany")
        self.assertEqual(rows[0].tickets, 1)
        self.assertEqual(rows[0].closed, 0)
        self.assertEqual(rows[0].open, 1)
        self.assertEqual(rows[0].revenue_cents, 0)
        self.assertIsNone(rows[0].dwell_p50_min)

    def test_empty_log_is_empty_list(self) -> None:
        self.assertEqual(by_origin([]), [])

    def test_sorts_by_revenue_then_origin(self) -> None:
        events = [
            *ticket_flow("t_z", at(8), 1000, prefix="z", vehicle="2018 Honda Civic"),
            *ticket_flow("t_a", at(9), 1000, prefix="a", vehicle="2021 Toyota RAV4"),
            *ticket_flow("t_m", at(10), 5000, prefix="m", vehicle="2015 Ford F-150"),
        ]
        self.assertEqual([row.origin for row in by_origin(events)], ["US", "Japan"])
        self.assertEqual(by_origin(events)[1].tickets, 2)

    def test_seeded_day_covers_all_tickets(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        rows = by_origin(events)
        tickets = project(events)
        self.assertEqual(sum(row.tickets for row in rows), len(tickets))
        expected = sum(t.total_cents for t in tickets.values() if t.closed)
        self.assertEqual(sum(row.revenue_cents for row in rows), expected)
        self.assertGreater(expected, 0)
        leftover = sum(1 for t in tickets.values() if not t.closed)
        self.assertEqual(sum(row.open for row in rows), leftover)
        names = {row.origin for row in rows}
        self.assertTrue({"Japan", "US", "Korea", "Germany"} <= names)
        self.assertNotIn("", names)


class OriginRenderTests(unittest.TestCase):
    def test_human_and_json_match_fold(self) -> None:
        events = [
            *ticket_flow(
                "t_a", at(8), 6999, prefix="a", vehicle="2018 Honda Civic"
            ),
            *ticket_flow(
                "t_b", at(9), 1299, prefix="b", vehicle="2015 Ford F-150"
            ),
        ]
        rows = by_origin(events)
        text = render_origin(events, rows)
        self.assertIn("origin", text)
        self.assertIn("2 origins", text)
        self.assertIn("2 tickets", text)
        self.assertIn("Japan", text)
        self.assertIn("US", text)
        payload = json.loads(render_origin_json(events, rows))
        self.assertEqual(payload["events"], len(events))
        self.assertEqual(
            [(r["origin"], r["tickets"], r["revenue_cents"]) for r in payload["origins"]],
            [(row.origin, row.tickets, row.revenue_cents) for row in rows],
        )
