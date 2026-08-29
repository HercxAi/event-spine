from __future__ import annotations

import json
import unittest
from datetime import timedelta

from event_spine.events import EventType
from event_spine.project import project
from event_spine.report import render_year, render_year_json
from event_spine.simulate import SimConfig, simulate_day
from event_spine.year import by_year, year_of
from tests.helpers import at, ev, ticket_flow


class YearOfTests(unittest.TestCase):
    def test_pulls_leading_year(self) -> None:
        self.assertEqual(year_of("2018 Honda Civic"), "2018")
        self.assertEqual(year_of("2015 Ford F-150"), "2015")
        self.assertEqual(year_of("2017 BMW 328i"), "2017")
        self.assertEqual(year_of("2014 Jeep Grand Cherokee"), "2014")

    def test_bare_name_stays_empty(self) -> None:
        self.assertEqual(year_of("Zebra"), "")
        self.assertEqual(year_of("Chevy Silverado"), "")

    def test_empty_stays_empty(self) -> None:
        self.assertEqual(year_of(""), "")
        self.assertEqual(year_of("   "), "")


class YearFoldTests(unittest.TestCase):
    def test_groups_by_parsed_year(self) -> None:
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
                vehicle="2018 Honda CR-V",
            ),
        ]
        rows = {row.year: row for row in by_year(events)}
        self.assertEqual(set(rows), {"2018", "2015"})

        y2018 = rows["2018"]
        self.assertEqual(y2018.tickets, 2)
        self.assertEqual(y2018.closed, 1)
        self.assertEqual(y2018.open, 1)
        self.assertEqual(y2018.revenue_cents, 4000)
        self.assertAlmostEqual(y2018.dwell_p50_min or 0.0, 30.0)

        y2015 = rows["2015"]
        self.assertEqual(y2015.tickets, 1)
        self.assertEqual(y2015.closed, 1)
        self.assertEqual(y2015.open, 0)
        self.assertEqual(y2015.revenue_cents, 8000)
        self.assertAlmostEqual(y2015.dwell_p50_min or 0.0, 60.0)

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
        rows = by_year(events)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].year, "2021")
        self.assertEqual(rows[0].tickets, 1)
        self.assertEqual(rows[0].closed, 0)
        self.assertEqual(rows[0].open, 1)
        self.assertEqual(rows[0].revenue_cents, 0)
        self.assertIsNone(rows[0].dwell_p50_min)

    def test_empty_log_is_empty_list(self) -> None:
        self.assertEqual(by_year([]), [])

    def test_sorts_by_revenue_then_year(self) -> None:
        events = [
            *ticket_flow("t_z", at(8), 1000, prefix="z", vehicle="2010 Zebra"),
            *ticket_flow("t_a", at(9), 1000, prefix="a", vehicle="2018 Alpha"),
            *ticket_flow("t_m", at(10), 5000, prefix="m", vehicle="2020 Mike"),
        ]
        self.assertEqual(
            [row.year for row in by_year(events)],
            ["2020", "2010", "2018"],
        )

    def test_seeded_day_covers_all_tickets(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        rows = by_year(events)
        tickets = project(events)
        self.assertEqual(sum(row.tickets for row in rows), len(tickets))
        expected = sum(t.total_cents for t in tickets.values() if t.closed)
        self.assertEqual(sum(row.revenue_cents for row in rows), expected)
        self.assertGreater(expected, 0)
        leftover = sum(1 for t in tickets.values() if not t.closed)
        self.assertEqual(sum(row.open for row in rows), leftover)
        self.assertGreaterEqual(len(rows), 2)
        names = {row.year for row in rows}
        self.assertIn("2018", names)
        self.assertIn("2019", names)
        self.assertIn("2022", names)
        self.assertNotIn("", names)


class YearRenderTests(unittest.TestCase):
    def test_human_and_json_match_fold(self) -> None:
        events = [
            *ticket_flow(
                "t_a", at(8), 6999, prefix="a", vehicle="2018 Honda Civic"
            ),
            *ticket_flow(
                "t_b", at(9), 1299, prefix="b", vehicle="2015 Ford F-150"
            ),
        ]
        rows = by_year(events)
        text = render_year(events, rows)
        self.assertIn("year", text)
        self.assertIn("2 years", text)
        self.assertIn("2 tickets", text)
        self.assertIn("2018", text)
        self.assertIn("2015", text)
        payload = json.loads(render_year_json(events, rows))
        self.assertEqual(payload["events"], len(events))
        self.assertEqual(
            [(r["year"], r["tickets"], r["revenue_cents"]) for r in payload["years"]],
            [(row.year, row.tickets, row.revenue_cents) for row in rows],
        )
