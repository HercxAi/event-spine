from __future__ import annotations

import json
import unittest
from datetime import timedelta

from event_spine.age import by_age, age_of
from event_spine.events import EventType
from event_spine.project import project
from event_spine.report import render_age, render_age_json
from event_spine.simulate import SimConfig, simulate_day
from tests.helpers import at, ev, ticket_flow


class AgeOfTests(unittest.TestCase):
    def test_classifies_seeded_years(self) -> None:
        self.assertEqual(age_of("2022 Ford Escape", 2026), "0-4")
        self.assertEqual(age_of("2021 Toyota RAV4", 2026), "5-9")
        self.assertEqual(age_of("2018 Honda Civic", 2026), "5-9")
        self.assertEqual(age_of("2015 Ford F-150", 2026), "10-14")
        self.assertEqual(age_of("2011 Toyota Camry", 2026), "15-19")
        self.assertEqual(age_of("2005 Honda Civic", 2026), "20+")

    def test_future_model_year_is_new(self) -> None:
        self.assertEqual(age_of("2027 Honda Civic", 2026), "0-4")

    def test_empty_and_no_year_stay_empty(self) -> None:
        self.assertEqual(age_of("", 2026), "")
        self.assertEqual(age_of("   ", 2026), "")
        self.assertEqual(age_of("Honda Civic", 2026), "")


class AgeFoldTests(unittest.TestCase):
    def test_groups_by_classified_age(self) -> None:
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
                vehicle="2022 Ford Escape",
            ),
        ]
        rows = {row.age: row for row in by_age(events)}
        self.assertEqual(set(rows), {"5-9", "10-14", "0-4"})

        mid = rows["5-9"]
        self.assertEqual(mid.tickets, 1)
        self.assertEqual(mid.closed, 1)
        self.assertEqual(mid.open, 0)
        self.assertEqual(mid.revenue_cents, 4000)
        self.assertAlmostEqual(mid.dwell_p50_min or 0.0, 30.0)

        older = rows["10-14"]
        self.assertEqual(older.tickets, 1)
        self.assertEqual(older.closed, 1)
        self.assertEqual(older.open, 0)
        self.assertEqual(older.revenue_cents, 8000)
        self.assertAlmostEqual(older.dwell_p50_min or 0.0, 60.0)

        newest = rows["0-4"]
        self.assertEqual(newest.tickets, 1)
        self.assertEqual(newest.closed, 0)
        self.assertEqual(newest.open, 1)
        self.assertEqual(newest.revenue_cents, 0)
        self.assertIsNone(newest.dwell_p50_min)

    def test_open_ticket_skips_revenue_and_dwell(self) -> None:
        events = [
            ev(
                "e1",
                EventType.TICKET_OPENED,
                at(8),
                "t1",
                bay="3",
                vehicle="2011 Toyota Camry",
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
        rows = by_age(events)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].age, "15-19")
        self.assertEqual(rows[0].tickets, 1)
        self.assertEqual(rows[0].closed, 0)
        self.assertEqual(rows[0].open, 1)
        self.assertEqual(rows[0].revenue_cents, 0)
        self.assertIsNone(rows[0].dwell_p50_min)

    def test_empty_log_is_empty_list(self) -> None:
        self.assertEqual(by_age([]), [])

    def test_sorts_by_revenue_then_newest_band(self) -> None:
        events = [
            *ticket_flow("t_z", at(8), 1000, prefix="z", vehicle="2018 Honda Civic"),
            *ticket_flow("t_a", at(9), 1000, prefix="a", vehicle="2021 Toyota RAV4"),
            *ticket_flow("t_m", at(10), 5000, prefix="m", vehicle="2015 Ford F-150"),
        ]
        self.assertEqual([row.age for row in by_age(events)], ["10-14", "5-9"])
        self.assertEqual(by_age(events)[1].tickets, 2)

    def test_seeded_day_covers_all_tickets(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        rows = by_age(events)
        tickets = project(events)
        self.assertEqual(sum(row.tickets for row in rows), len(tickets))
        expected = sum(t.total_cents for t in tickets.values() if t.closed)
        self.assertEqual(sum(row.revenue_cents for row in rows), expected)
        self.assertGreater(expected, 0)
        leftover = sum(1 for t in tickets.values() if not t.closed)
        self.assertEqual(sum(row.open for row in rows), leftover)
        names = {row.age for row in rows}
        self.assertTrue({"0-4", "5-9", "10-14", "15-19"} <= names)
        self.assertNotIn("", names)


class AgeRenderTests(unittest.TestCase):
    def test_human_and_json_match_fold(self) -> None:
        events = [
            *ticket_flow(
                "t_a", at(8), 6999, prefix="a", vehicle="2018 Honda Civic"
            ),
            *ticket_flow(
                "t_b", at(9), 1299, prefix="b", vehicle="2015 Ford F-150"
            ),
        ]
        rows = by_age(events)
        text = render_age(events, rows)
        self.assertIn("age", text)
        self.assertIn("2 ages", text)
        self.assertIn("2 tickets", text)
        self.assertIn("5-9", text)
        self.assertIn("10-14", text)
        payload = json.loads(render_age_json(events, rows))
        self.assertEqual(payload["events"], len(events))
        self.assertEqual(
            [(r["age"], r["tickets"], r["revenue_cents"]) for r in payload["ages"]],
            [(row.age, row.tickets, row.revenue_cents) for row in rows],
        )
