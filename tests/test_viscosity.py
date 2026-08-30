from __future__ import annotations

import json
import unittest
from datetime import timedelta

from event_spine.events import EventType
from event_spine.project import LineItem, project
from event_spine.report import render_viscosity, render_viscosity_json
from event_spine.simulate import SimConfig, simulate_day
from event_spine.stats import percentile
from event_spine.viscosity import ViscosityRow, by_viscosity, viscosity_of
from tests.helpers import at, ev, ticket_flow


class ViscosityOfTests(unittest.TestCase):
    def test_classifies_oil_skus(self) -> None:
        self.assertEqual(viscosity_of(["OIL-CONV"]), "5W-30")
        self.assertEqual(viscosity_of(["OIL-SYN"]), "5W-30")
        self.assertEqual(viscosity_of(["OIL-FS"]), "0W-20")

    def test_parses_sae_from_description(self) -> None:
        self.assertEqual(
            viscosity_of(LineItem("OIL-CONV", "Conventional 5W-30", 1, 3999)),
            "5W-30",
        )
        self.assertEqual(
            viscosity_of(LineItem("OIL-SYN", "Synthetic 5W-30", 1, 6999)),
            "5W-30",
        )
        self.assertEqual(
            viscosity_of(LineItem("OIL-FS", "Full synthetic 0W-20", 1, 8499)),
            "0W-20",
        )
        self.assertEqual(
            viscosity_of(LineItem("OIL-FS", "full synthetic 0w-20", 1, 8499)),
            "0W-20",
        )

    def test_falls_back_from_sku_when_description_has_no_sae(self) -> None:
        self.assertEqual(
            viscosity_of(LineItem("OIL-CONV", "OIL-CONV", 1, 3999)),
            "5W-30",
        )
        self.assertEqual(
            viscosity_of(LineItem("OIL-SYN", "OIL-SYN", 1, 6999)),
            "5W-30",
        )
        self.assertEqual(
            viscosity_of(LineItem("OIL-FS", "OIL-FS", 1, 8499)),
            "0W-20",
        )
        self.assertEqual(
            viscosity_of(LineItem("OIL-CONV", "oil", 1, 3999)),
            "5W-30",
        )

    def test_description_beats_sku_fallback(self) -> None:
        self.assertEqual(
            viscosity_of(LineItem("OIL-CONV", "Conventional 10W-40", 1, 3999)),
            "10W-40",
        )

    def test_highest_grade_wins(self) -> None:
        self.assertEqual(viscosity_of(["OIL-CONV", "OIL-SYN"]), "5W-30")
        self.assertEqual(viscosity_of(["OIL-CONV", "OIL-FS"]), "0W-20")
        self.assertEqual(viscosity_of(["OIL-SYN", "OIL-FS", "OIL-CONV"]), "0W-20")
        self.assertEqual(viscosity_of(["FIL-OIL", "OIL-CONV"]), "5W-30")
        items = (
            LineItem("OIL-CONV", "Conventional 5W-30", 1, 3999),
            LineItem("OIL-FS", "Full synthetic 0W-20", 1, 8499),
        )
        self.assertEqual(viscosity_of(items), "0W-20")

    def test_empty_items_stay_empty(self) -> None:
        self.assertEqual(viscosity_of([]), "")
        self.assertEqual(viscosity_of(["FIL-OIL", "INSP"]), "")
        self.assertEqual(viscosity_of(""), "")

    def test_accepts_line_items_and_ticket(self) -> None:
        items = (
            LineItem("OIL-SYN", "Synthetic 5W-30", 1, 6999),
            LineItem("FIL-OIL", "Oil filter", 1, 1299),
        )
        self.assertEqual(viscosity_of(items), "5W-30")
        self.assertEqual(viscosity_of(items[0]), "5W-30")
        events = ticket_flow(
            "t_fs",
            at(8),
            8499,
            prefix="w",
            items=[("OIL-FS", 8499), ("TRN-FLUSH", 18900)],
        )
        ticket = project(events)["t_fs"]
        self.assertEqual(viscosity_of(ticket), "0W-20")
        self.assertEqual(viscosity_of(ticket.items), "0W-20")


class ViscosityFoldTests(unittest.TestCase):
    def test_groups_by_classified_viscosity(self) -> None:
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
        rows = {row.viscosity: row for row in by_viscosity(events)}
        self.assertEqual(set(rows), {"5W-30", "0W-20"})

        mixed = rows["5W-30"]
        self.assertEqual(mixed.tickets, 2)
        self.assertEqual(mixed.closed, 2)
        self.assertEqual(mixed.open, 0)
        self.assertEqual(mixed.revenue_cents, 12000)
        self.assertAlmostEqual(mixed.dwell_p50_min or 0.0, 45.0)

        fs = rows["0W-20"]
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
        rows = by_viscosity(events)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].viscosity, "5W-30")
        self.assertEqual(rows[0].tickets, 1)
        self.assertEqual(rows[0].closed, 0)
        self.assertEqual(rows[0].open, 1)
        self.assertEqual(rows[0].revenue_cents, 0)
        self.assertIsNone(rows[0].dwell_p50_min)

    def test_empty_log_is_empty_list(self) -> None:
        self.assertEqual(by_viscosity([]), [])

    def test_sorts_by_revenue_then_viscosity(self) -> None:
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
            [row.viscosity for row in by_viscosity(events)],
            ["0W-20", "5W-30"],
        )
        self.assertEqual(by_viscosity(events)[1].tickets, 2)

    def test_tie_breaks_alphabetically(self) -> None:
        events = [
            *ticket_flow(
                "t_c", at(8), 2000, prefix="c", items=[("OIL-CONV", 2000)]
            ),
            *ticket_flow(
                "t_s", at(9), 2000, prefix="s", items=[("OIL-FS", 2000)]
            ),
        ]
        self.assertEqual(
            [row.viscosity for row in by_viscosity(events)],
            ["0W-20", "5W-30"],
        )

    def test_seeded_day_matches_manual_projection(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        rows = by_viscosity(events)
        tickets = project(events)
        self.assertEqual(sum(row.tickets for row in rows), len(tickets))
        expected = sum(t.total_cents for t in tickets.values() if t.closed)
        self.assertEqual(sum(row.revenue_cents for row in rows), expected)
        self.assertGreater(expected, 0)
        leftover = sum(1 for t in tickets.values() if not t.closed)
        self.assertEqual(sum(row.open for row in rows), leftover)
        names = {row.viscosity for row in rows}
        self.assertTrue({"5W-30", "0W-20"} <= names)

        by: dict[str, list] = {}
        for ticket in tickets.values():
            by.setdefault(viscosity_of(ticket.items), []).append(ticket)
        manual: list[ViscosityRow] = []
        for viscosity, group in by.items():
            closed = [t for t in group if t.closed]
            dwells = [
                (t.closed_at - t.opened_at).total_seconds() / 60.0
                for t in closed
                if t.closed_at is not None
            ]
            manual.append(
                ViscosityRow(
                    viscosity=viscosity,
                    tickets=len(group),
                    closed=len(closed),
                    open=len(group) - len(closed),
                    revenue_cents=sum(t.total_cents for t in closed),
                    dwell_p50_min=percentile(dwells, 0.50),
                )
            )
        manual.sort(key=lambda row: (-row.revenue_cents, row.viscosity))
        self.assertEqual(rows, manual)


class ViscosityRenderTests(unittest.TestCase):
    def test_human_and_json_match_fold(self) -> None:
        events = [
            *ticket_flow(
                "t_a", at(8), 6999, prefix="a", items=[("OIL-SYN", 6999)]
            ),
            *ticket_flow(
                "t_b", at(9), 1299, prefix="b", items=[("FIL-OIL", 1299)]
            ),
        ]
        rows = by_viscosity(events)
        text = render_viscosity(events, rows)
        self.assertIn("viscosity", text)
        self.assertIn("2 viscosities", text)
        self.assertIn("2 tickets", text)
        self.assertIn("5W-30", text)
        self.assertIn("—", text)
        payload = json.loads(render_viscosity_json(events, rows))
        self.assertEqual(payload["events"], len(events))
        self.assertIn("viscosities", payload)
        self.assertEqual(
            [(r["viscosity"], r["tickets"], r["revenue_cents"]) for r in payload["viscosities"]],
            [(row.viscosity, row.tickets, row.revenue_cents) for row in rows],
        )
