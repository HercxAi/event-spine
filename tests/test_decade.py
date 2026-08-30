from __future__ import annotations

import json
import unittest
from datetime import timedelta

from event_spine.decade import by_decade, decade_of
from event_spine.events import EventType
from event_spine.project import project
from event_spine.report import render_decade, render_decade_json
from event_spine.simulate import SimConfig, simulate_day
from tests.helpers import at, ev, ticket_flow


class DecadeOfTests(unittest.TestCase):
    def test_floors_model_year(self) -> None:
        self.assertEqual(decade_of("2018 Honda Civic"), "2010s")
        self.assertEqual(decade_of("2011 Toyota Camry"), "2010s")
        self.assertEqual(decade_of("2019 Subaru Outback"), "2010s")
        self.assertEqual(decade_of("2020 Hyundai Tucson"), "2020s")
        self.assertEqual(decade_of("2022 Ford Escape"), "2020s")
        self.assertEqual(decade_of("2005 Honda Accord"), "2000s")

    def test_empty_and_unparseable_stay_empty(self) -> None:
        self.assertEqual(decade_of(""), "")
        self.assertEqual(decade_of("   "), "")
        self.assertEqual(decade_of("Honda Civic"), "")


class DecadeFoldTests(unittest.TestCase):
    def test_groups_by_decade(self) -> None:
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
                vehicle="2021 Toyota RAV4",
                dwell=timedelta(minutes=60),
            ),
            ev(
                "o1",
                EventType.TICKET_OPENED,
                at(10),
                "t_open",
                bay="1",
                vehicle="2015 Ford F-150",
            ),
        ]
        rows = {row.decade: row for row in by_decade(events)}
        self.assertEqual(set(rows), {"2010s", "2020s"})

        tens = rows["2010s"]
        self.assertEqual(tens.tickets, 2)
        self.assertEqual(tens.closed, 1)
        self.assertEqual(tens.open, 1)
        self.assertEqual(tens.revenue_cents, 4000)
        self.assertAlmostEqual(tens.dwell_p50_min or 0.0, 30.0)

        twenties = rows["2020s"]
        self.assertEqual(twenties.tickets, 1)
        self.assertEqual(twenties.closed, 1)
        self.assertEqual(twenties.open, 0)
        self.assertEqual(twenties.revenue_cents, 8000)
        self.assertAlmostEqual(twenties.dwell_p50_min or 0.0, 60.0)

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
        rows = by_decade(events)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].decade, "2010s")
        self.assertEqual(rows[0].tickets, 1)
        self.assertEqual(rows[0].closed, 0)
        self.assertEqual(rows[0].open, 1)
        self.assertEqual(rows[0].revenue_cents, 0)
        self.assertIsNone(rows[0].dwell_p50_min)

    def test_empty_log_is_empty_list(self) -> None:
        self.assertEqual(by_decade([]), [])

    def test_sorts_by_revenue_then_newest_decade(self) -> None:
        events = [
            *ticket_flow("t_z", at(8), 1000, prefix="z", vehicle="2018 Honda Civic"),
            *ticket_flow("t_a", at(9), 1000, prefix="a", vehicle="2015 Ford F-150"),
            *ticket_flow("t_m", at(10), 5000, prefix="m", vehicle="2021 Toyota RAV4"),
        ]
        self.assertEqual([row.decade for row in by_decade(events)], ["2020s", "2010s"])
        self.assertEqual(by_decade(events)[1].tickets, 2)

    def test_seeded_day_covers_all_tickets(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        rows = by_decade(events)
        tickets = project(events)
        self.assertEqual(sum(row.tickets for row in rows), len(tickets))
        expected = sum(t.total_cents for t in tickets.values() if t.closed)
        self.assertEqual(sum(row.revenue_cents for row in rows), expected)
        self.assertGreater(expected, 0)
        leftover = sum(1 for t in tickets.values() if not t.closed)
        self.assertEqual(sum(row.open for row in rows), leftover)
        names = {row.decade for row in rows}
        self.assertTrue({"2010s", "2020s"} <= names)
        self.assertNotIn("", names)


class DecadeRenderTests(unittest.TestCase):
    def test_human_and_json_match_fold(self) -> None:
        events = [
            *ticket_flow(
                "t_a", at(8), 6999, prefix="a", vehicle="2018 Honda Civic"
            ),
            *ticket_flow(
                "t_b", at(9), 1299, prefix="b", vehicle="2021 Toyota RAV4"
            ),
        ]
        rows = by_decade(events)
        text = render_decade(events, rows)
        self.assertIn("decade", text)
        self.assertIn("2 decades", text)
        self.assertIn("2 tickets", text)
        self.assertIn("2010s", text)
        self.assertIn("2020s", text)
        payload = json.loads(render_decade_json(events, rows))
        self.assertEqual(payload["events"], len(events))
        self.assertEqual(
            [(r["decade"], r["tickets"], r["revenue_cents"]) for r in payload["decades"]],
            [(row.decade, row.tickets, row.revenue_cents) for row in rows],
        )
