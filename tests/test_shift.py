from __future__ import annotations

import json
import unittest
from datetime import timedelta

from event_spine.events import EventType
from event_spine.project import project
from event_spine.report import render_shift, render_shift_json
from event_spine.shift import ShiftRow, by_shift, shift_of
from event_spine.simulate import SimConfig, simulate_day
from event_spine.stats import percentile
from tests.helpers import at, ev, ticket_flow


class ShiftOfTests(unittest.TestCase):
    def test_classifies_hour_bands(self) -> None:
        for hour in (7, 8, 9, 10):
            self.assertEqual(shift_of(hour), "morning", hour)
        for hour in (11, 12, 13):
            self.assertEqual(shift_of(hour), "midday", hour)
        for hour in (14, 15, 16, 17, 18):
            self.assertEqual(shift_of(hour), "afternoon", hour)

    def test_outside_shop_and_missing_stay_empty(self) -> None:
        self.assertEqual(shift_of(6), "")
        self.assertEqual(shift_of(19), "")
        self.assertEqual(shift_of(None), "")
        self.assertEqual(shift_of(""), "")

    def test_accepts_datetime_event_and_ticket(self) -> None:
        self.assertEqual(shift_of(at(8)), "morning")
        self.assertEqual(shift_of(at(12)), "midday")
        self.assertEqual(shift_of(at(16)), "afternoon")
        opened = ev(
            "e1",
            EventType.TICKET_OPENED,
            at(9),
            "t1",
            bay="1",
            vehicle="2018 Honda Civic",
        )
        self.assertEqual(shift_of(opened), "morning")
        events = ticket_flow("t_m", at(12), 4000, prefix="m")
        ticket = project(events)["t_m"]
        self.assertEqual(shift_of(ticket), "midday")


class ShiftFoldTests(unittest.TestCase):
    def test_groups_by_classified_shift(self) -> None:
        events = [
            *ticket_flow(
                "t_a",
                at(8),
                4000,
                prefix="a",
                dwell=timedelta(minutes=30),
            ),
            *ticket_flow(
                "t_b",
                at(12),
                8000,
                prefix="b",
                dwell=timedelta(minutes=60),
            ),
            ev(
                "o1",
                EventType.TICKET_OPENED,
                at(16),
                "t_open",
                bay="1",
                vehicle="2017 BMW 328i",
            ),
        ]
        rows = {row.shift: row for row in by_shift(events)}
        self.assertEqual(set(rows), {"morning", "midday", "afternoon"})

        morning = rows["morning"]
        self.assertEqual(morning.tickets, 1)
        self.assertEqual(morning.closed, 1)
        self.assertEqual(morning.open, 0)
        self.assertEqual(morning.revenue_cents, 4000)
        self.assertAlmostEqual(morning.dwell_p50_min or 0.0, 30.0)

        midday = rows["midday"]
        self.assertEqual(midday.tickets, 1)
        self.assertEqual(midday.closed, 1)
        self.assertEqual(midday.open, 0)
        self.assertEqual(midday.revenue_cents, 8000)
        self.assertAlmostEqual(midday.dwell_p50_min or 0.0, 60.0)

        afternoon = rows["afternoon"]
        self.assertEqual(afternoon.tickets, 1)
        self.assertEqual(afternoon.closed, 0)
        self.assertEqual(afternoon.open, 1)
        self.assertEqual(afternoon.revenue_cents, 0)
        self.assertIsNone(afternoon.dwell_p50_min)

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
        rows = by_shift(events)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].shift, "morning")
        self.assertEqual(rows[0].tickets, 1)
        self.assertEqual(rows[0].closed, 0)
        self.assertEqual(rows[0].open, 1)
        self.assertEqual(rows[0].revenue_cents, 0)
        self.assertIsNone(rows[0].dwell_p50_min)

    def test_empty_log_is_empty_list(self) -> None:
        self.assertEqual(by_shift([]), [])

    def test_sorts_by_revenue_then_shift_order(self) -> None:
        events = [
            *ticket_flow("t_z", at(8), 1000, prefix="z"),
            *ticket_flow("t_a", at(9), 1000, prefix="a"),
            *ticket_flow("t_m", at(12), 5000, prefix="m"),
        ]
        self.assertEqual(
            [row.shift for row in by_shift(events)],
            ["midday", "morning"],
        )
        self.assertEqual(by_shift(events)[1].tickets, 2)

    def test_tie_breaks_morning_midday_afternoon(self) -> None:
        events = [
            *ticket_flow("t_a", at(16), 2000, prefix="a"),
            *ticket_flow("t_m", at(8), 2000, prefix="m"),
            *ticket_flow("t_n", at(12), 2000, prefix="n"),
        ]
        self.assertEqual(
            [row.shift for row in by_shift(events)],
            ["morning", "midday", "afternoon"],
        )

    def test_seeded_day_covers_all_tickets(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        rows = by_shift(events)
        tickets = project(events)
        self.assertEqual(sum(row.tickets for row in rows), len(tickets))
        expected = sum(t.total_cents for t in tickets.values() if t.closed)
        self.assertEqual(sum(row.revenue_cents for row in rows), expected)
        self.assertGreater(expected, 0)
        leftover = sum(1 for t in tickets.values() if not t.closed)
        self.assertEqual(sum(row.open for row in rows), leftover)
        names = {row.shift for row in rows}
        self.assertTrue({"morning", "midday", "afternoon"} <= names)
        self.assertNotIn("", names)

        by: dict[str, list] = {}
        for ticket in tickets.values():
            by.setdefault(shift_of(ticket), []).append(ticket)
        manual: list[ShiftRow] = []
        for shift, group in by.items():
            closed = [t for t in group if t.closed]
            dwells = [
                (t.closed_at - t.opened_at).total_seconds() / 60.0
                for t in closed
                if t.closed_at is not None
            ]
            manual.append(
                ShiftRow(
                    shift=shift,
                    tickets=len(group),
                    closed=len(closed),
                    open=len(group) - len(closed),
                    revenue_cents=sum(t.total_cents for t in closed),
                    dwell_p50_min=percentile(dwells, 0.50),
                )
            )
        order = {"morning": 0, "midday": 1, "afternoon": 2}
        manual.sort(
            key=lambda row: (
                -row.revenue_cents,
                order.get(row.shift, 99),
                row.shift,
            )
        )
        self.assertEqual(rows, manual)


class ShiftRenderTests(unittest.TestCase):
    def test_human_and_json_match_fold(self) -> None:
        events = [
            *ticket_flow("t_a", at(8), 6999, prefix="a"),
            *ticket_flow("t_b", at(16), 1299, prefix="b"),
        ]
        rows = by_shift(events)
        text = render_shift(events, rows)
        self.assertIn("shift", text)
        self.assertIn("2 shifts", text)
        self.assertIn("2 tickets", text)
        self.assertIn("morning", text)
        self.assertIn("afternoon", text)
        payload = json.loads(render_shift_json(events, rows))
        self.assertEqual(payload["events"], len(events))
        self.assertIn("shop", payload)
        self.assertIn("day", payload)
        self.assertIn("shifts", payload)
        self.assertEqual(
            [(r["shift"], r["tickets"], r["revenue_cents"]) for r in payload["shifts"]],
            [(row.shift, row.tickets, row.revenue_cents) for row in rows],
        )
