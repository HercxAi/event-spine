from __future__ import annotations

import json
import unittest
from datetime import timedelta

from event_spine.events import EventType
from event_spine.model import by_model, model_of
from event_spine.project import project
from event_spine.report import render_model, render_model_json
from event_spine.simulate import SimConfig, simulate_day
from tests.helpers import at, ev, ticket_flow


class ModelOfTests(unittest.TestCase):
    def test_skips_year_and_make(self) -> None:
        self.assertEqual(model_of("2018 Honda Civic"), "Civic")
        self.assertEqual(model_of("2015 Ford F-150"), "F-150")
        self.assertEqual(model_of("2017 BMW 328i"), "328i")
        self.assertEqual(model_of("2014 Jeep Grand Cherokee"), "Grand Cherokee")

    def test_bare_name_drops_make_keeps_rest(self) -> None:
        self.assertEqual(model_of("Chevy Silverado"), "Silverado")
        self.assertEqual(model_of("Zebra"), "")

    def test_empty_stays_empty(self) -> None:
        self.assertEqual(model_of(""), "")
        self.assertEqual(model_of("   "), "")


class ModelFoldTests(unittest.TestCase):
    def test_groups_by_parsed_model(self) -> None:
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
        rows = {row.model: row for row in by_model(events)}
        self.assertEqual(set(rows), {"Civic", "F-150", "CR-V"})

        civic = rows["Civic"]
        self.assertEqual(civic.tickets, 1)
        self.assertEqual(civic.closed, 1)
        self.assertEqual(civic.open, 0)
        self.assertEqual(civic.revenue_cents, 4000)
        self.assertAlmostEqual(civic.dwell_p50_min or 0.0, 30.0)

        f150 = rows["F-150"]
        self.assertEqual(f150.tickets, 1)
        self.assertEqual(f150.closed, 1)
        self.assertEqual(f150.open, 0)
        self.assertEqual(f150.revenue_cents, 8000)
        self.assertAlmostEqual(f150.dwell_p50_min or 0.0, 60.0)

        crv = rows["CR-V"]
        self.assertEqual(crv.tickets, 1)
        self.assertEqual(crv.closed, 0)
        self.assertEqual(crv.open, 1)
        self.assertEqual(crv.revenue_cents, 0)
        self.assertIsNone(crv.dwell_p50_min)

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
        rows = by_model(events)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].model, "RAV4")
        self.assertEqual(rows[0].tickets, 1)
        self.assertEqual(rows[0].closed, 0)
        self.assertEqual(rows[0].open, 1)
        self.assertEqual(rows[0].revenue_cents, 0)
        self.assertIsNone(rows[0].dwell_p50_min)

    def test_empty_log_is_empty_list(self) -> None:
        self.assertEqual(by_model([]), [])

    def test_sorts_by_revenue_then_model(self) -> None:
        events = [
            *ticket_flow("t_z", at(8), 1000, prefix="z", vehicle="Make Zulu"),
            *ticket_flow("t_a", at(9), 1000, prefix="a", vehicle="Make Alpha"),
            *ticket_flow("t_m", at(10), 5000, prefix="m", vehicle="Make Mike"),
        ]
        self.assertEqual(
            [row.model for row in by_model(events)],
            ["Mike", "Alpha", "Zulu"],
        )

    def test_seeded_day_covers_all_tickets(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        rows = by_model(events)
        tickets = project(events)
        self.assertEqual(sum(row.tickets for row in rows), len(tickets))
        expected = sum(t.total_cents for t in tickets.values() if t.closed)
        self.assertEqual(sum(row.revenue_cents for row in rows), expected)
        self.assertGreater(expected, 0)
        leftover = sum(1 for t in tickets.values() if not t.closed)
        self.assertEqual(sum(row.open for row in rows), leftover)
        self.assertGreaterEqual(len(rows), 2)
        names = {row.model for row in rows}
        self.assertIn("Civic", names)
        self.assertIn("F-150", names)
        self.assertNotIn("", names)


class ModelRenderTests(unittest.TestCase):
    def test_human_and_json_match_fold(self) -> None:
        events = [
            *ticket_flow(
                "t_a", at(8), 6999, prefix="a", vehicle="2018 Honda Civic"
            ),
            *ticket_flow(
                "t_b", at(9), 1299, prefix="b", vehicle="2015 Ford F-150"
            ),
        ]
        rows = by_model(events)
        text = render_model(events, rows)
        self.assertIn("model", text)
        self.assertIn("2 models", text)
        self.assertIn("2 tickets", text)
        self.assertIn("Civic", text)
        self.assertIn("F-150", text)
        payload = json.loads(render_model_json(events, rows))
        self.assertEqual(payload["events"], len(events))
        self.assertEqual(
            [(r["model"], r["tickets"], r["revenue_cents"]) for r in payload["models"]],
            [(row.model, row.tickets, row.revenue_cents) for row in rows],
        )
