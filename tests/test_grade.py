from __future__ import annotations

import json
import unittest
from datetime import timedelta

from event_spine.events import EventType
from event_spine.grade import GradeRow, by_grade, grade_of
from event_spine.project import LineItem, project
from event_spine.report import render_grade, render_grade_json
from event_spine.simulate import SimConfig, simulate_day
from event_spine.stats import percentile
from tests.helpers import at, ev, ticket_flow


class GradeOfTests(unittest.TestCase):
    def test_classifies_oil_skus(self) -> None:
        self.assertEqual(grade_of(["OIL-CONV"]), "conventional")
        self.assertEqual(grade_of(["OIL-SYN"]), "synthetic")
        self.assertEqual(grade_of(["OIL-FS"]), "full-synth")

    def test_highest_grade_wins(self) -> None:
        self.assertEqual(grade_of(["OIL-CONV", "OIL-SYN"]), "synthetic")
        self.assertEqual(grade_of(["OIL-CONV", "OIL-FS"]), "full-synth")
        self.assertEqual(grade_of(["OIL-SYN", "OIL-FS", "OIL-CONV"]), "full-synth")
        self.assertEqual(grade_of(["FIL-OIL", "OIL-CONV"]), "conventional")

    def test_empty_items_stay_empty(self) -> None:
        self.assertEqual(grade_of([]), "")
        self.assertEqual(grade_of(["FIL-OIL", "INSP"]), "")
        self.assertEqual(grade_of(""), "")

    def test_accepts_line_items_and_ticket(self) -> None:
        items = (
            LineItem("OIL-SYN", "Synthetic 5W-30", 1, 6999),
            LineItem("FIL-OIL", "Oil filter", 1, 1299),
        )
        self.assertEqual(grade_of(items), "synthetic")
        self.assertEqual(grade_of(items[0]), "synthetic")
        events = ticket_flow(
            "t_fs",
            at(8),
            8499,
            prefix="w",
            items=[("OIL-FS", 8499), ("TRN-FLUSH", 18900)],
        )
        ticket = project(events)["t_fs"]
        self.assertEqual(grade_of(ticket), "full-synth")
        self.assertEqual(grade_of(ticket.items), "full-synth")


class GradeFoldTests(unittest.TestCase):
    def test_groups_by_classified_grade(self) -> None:
        events = [
            *ticket_flow(
                "t_a",
                at(8),
                4000,
                prefix="a",
                items=[("OIL-CONV", 4000)],
                dwell=timedelta(minutes=30),
            ),
            *ticket_flow(
                "t_b",
                at(9),
                8000,
                prefix="b",
                items=[("OIL-SYN", 8000)],
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
            ev(
                "o2",
                EventType.LINE_ITEM_ADDED,
                at(10, 1),
                "t_open",
                sku="OIL-FS",
                description="Full synthetic 0W-20",
                qty=1,
                unit_cents=8499,
            ),
        ]
        rows = {row.grade: row for row in by_grade(events)}
        self.assertEqual(set(rows), {"conventional", "synthetic", "full-synth"})

        conv = rows["conventional"]
        self.assertEqual(conv.tickets, 1)
        self.assertEqual(conv.closed, 1)
        self.assertEqual(conv.open, 0)
        self.assertEqual(conv.revenue_cents, 4000)
        self.assertAlmostEqual(conv.dwell_p50_min or 0.0, 30.0)

        syn = rows["synthetic"]
        self.assertEqual(syn.tickets, 1)
        self.assertEqual(syn.closed, 1)
        self.assertEqual(syn.open, 0)
        self.assertEqual(syn.revenue_cents, 8000)
        self.assertAlmostEqual(syn.dwell_p50_min or 0.0, 60.0)

        fs = rows["full-synth"]
        self.assertEqual(fs.tickets, 1)
        self.assertEqual(fs.closed, 0)
        self.assertEqual(fs.open, 1)
        self.assertEqual(fs.revenue_cents, 0)
        self.assertIsNone(fs.dwell_p50_min)

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
        rows = by_grade(events)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].grade, "conventional")
        self.assertEqual(rows[0].tickets, 1)
        self.assertEqual(rows[0].closed, 0)
        self.assertEqual(rows[0].open, 1)
        self.assertEqual(rows[0].revenue_cents, 0)
        self.assertIsNone(rows[0].dwell_p50_min)

    def test_empty_log_is_empty_list(self) -> None:
        self.assertEqual(by_grade([]), [])

    def test_sorts_by_revenue_then_grade(self) -> None:
        events = [
            *ticket_flow(
                "t_z", at(8), 1000, prefix="z", items=[("OIL-CONV", 1000)]
            ),
            *ticket_flow(
                "t_a", at(9), 1000, prefix="a", items=[("OIL-CONV", 1000)]
            ),
            *ticket_flow(
                "t_m", at(10), 5000, prefix="m", items=[("OIL-FS", 5000)]
            ),
        ]
        self.assertEqual(
            [row.grade for row in by_grade(events)],
            ["full-synth", "conventional"],
        )
        self.assertEqual(by_grade(events)[1].tickets, 2)

    def test_tie_breaks_alphabetically(self) -> None:
        events = [
            *ticket_flow(
                "t_c", at(8), 2000, prefix="c", items=[("OIL-CONV", 2000)]
            ),
            *ticket_flow(
                "t_s", at(9), 2000, prefix="s", items=[("OIL-SYN", 2000)]
            ),
        ]
        self.assertEqual(
            [row.grade for row in by_grade(events)],
            ["conventional", "synthetic"],
        )

    def test_seeded_day_matches_manual_projection(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        rows = by_grade(events)
        tickets = project(events)
        self.assertEqual(sum(row.tickets for row in rows), len(tickets))
        expected = sum(t.total_cents for t in tickets.values() if t.closed)
        self.assertEqual(sum(row.revenue_cents for row in rows), expected)
        self.assertGreater(expected, 0)
        leftover = sum(1 for t in tickets.values() if not t.closed)
        self.assertEqual(sum(row.open for row in rows), leftover)
        names = {row.grade for row in rows}
        self.assertTrue({"conventional", "synthetic", "full-synth"} <= names)

        by: dict[str, list] = {}
        for ticket in tickets.values():
            by.setdefault(grade_of(ticket.items), []).append(ticket)
        manual: list[GradeRow] = []
        for grade, group in by.items():
            closed = [t for t in group if t.closed]
            dwells = [
                (t.closed_at - t.opened_at).total_seconds() / 60.0
                for t in closed
                if t.closed_at is not None
            ]
            manual.append(
                GradeRow(
                    grade=grade,
                    tickets=len(group),
                    closed=len(closed),
                    open=len(group) - len(closed),
                    revenue_cents=sum(t.total_cents for t in closed),
                    dwell_p50_min=percentile(dwells, 0.50),
                )
            )
        manual.sort(key=lambda row: (-row.revenue_cents, row.grade))
        self.assertEqual(rows, manual)


class GradeRenderTests(unittest.TestCase):
    def test_human_and_json_match_fold(self) -> None:
        events = [
            *ticket_flow(
                "t_a", at(8), 6999, prefix="a", items=[("OIL-SYN", 6999)]
            ),
            *ticket_flow(
                "t_b", at(9), 1299, prefix="b", items=[("FIL-OIL", 1299)]
            ),
        ]
        rows = by_grade(events)
        text = render_grade(events, rows)
        self.assertIn("grade", text)
        self.assertIn("2 grades", text)
        self.assertIn("2 tickets", text)
        self.assertIn("synthetic", text)
        self.assertIn("—", text)
        payload = json.loads(render_grade_json(events, rows))
        self.assertEqual(payload["events"], len(events))
        self.assertIn("grades", payload)
        self.assertEqual(
            [(r["grade"], r["tickets"], r["revenue_cents"]) for r in payload["grades"]],
            [(row.grade, row.tickets, row.revenue_cents) for row in rows],
        )
