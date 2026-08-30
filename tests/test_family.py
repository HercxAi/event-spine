from __future__ import annotations

import json
import unittest
from datetime import timedelta

from event_spine.events import EventType
from event_spine.family import FamilyRow, by_family, family_of
from event_spine.project import LineItem, project
from event_spine.report import render_family, render_family_json
from event_spine.simulate import SimConfig, simulate_day
from event_spine.stats import percentile
from tests.helpers import at, ev, ticket_flow


class FamilyOfTests(unittest.TestCase):
    def test_classifies_catalog_skus(self) -> None:
        self.assertEqual(family_of(["OIL-CONV"]), "oil")
        self.assertEqual(family_of(["FIL-OIL"]), "filter")
        self.assertEqual(family_of(["WIP-STD"]), "wiper")
        self.assertEqual(family_of(["FLD-COOL"]), "service")
        self.assertEqual(family_of(["TRN-FLUSH"]), "service")
        self.assertEqual(family_of(["DIFF-FLUID"]), "service")
        self.assertEqual(family_of(["BRK-FLUSH"]), "service")

    def test_joins_present_families_in_fixed_order(self) -> None:
        self.assertEqual(family_of(["OIL-CONV", "FIL-OIL"]), "oil+filter")
        self.assertEqual(family_of(["FIL-OIL", "OIL-SYN", "WIP-STD"]), "oil+filter+wiper")
        self.assertEqual(
            family_of(["WIP-STD", "FLD-COOL", "OIL-FS", "FIL-AIR"]),
            "oil+filter+wiper+service",
        )
        self.assertEqual(family_of(["TRN-FLUSH", "OIL-FS", "FIL-OIL"]), "oil+filter+service")

    def test_skips_free_inspection(self) -> None:
        self.assertEqual(family_of(["INSP"]), "")
        self.assertEqual(family_of(["OIL-CONV", "FIL-OIL", "INSP"]), "oil+filter")
        self.assertEqual(family_of(["INSP", "WIP-STD"]), "wiper")

    def test_empty_items_stay_empty(self) -> None:
        self.assertEqual(family_of([]), "")
        self.assertEqual(family_of(""), "")
        self.assertEqual(family_of(["INSP", "INSP"]), "")

    def test_accepts_line_items_and_ticket(self) -> None:
        items = (
            LineItem("OIL-SYN", "Synthetic 5W-30", 1, 6999),
            LineItem("FIL-OIL", "Oil filter", 1, 1299),
            LineItem("INSP", "Multi-point inspection", 1, 0),
        )
        self.assertEqual(family_of(items), "oil+filter")
        self.assertEqual(family_of(items[0]), "oil")
        events = ticket_flow(
            "t_fs",
            at(8),
            8499,
            prefix="w",
            items=[("OIL-FS", 8499), ("TRN-FLUSH", 18900)],
        )
        ticket = project(events)["t_fs"]
        self.assertEqual(family_of(ticket), "oil+service")
        self.assertEqual(family_of(ticket.items), "oil+service")


class FamilyFoldTests(unittest.TestCase):
    def test_groups_by_classified_family(self) -> None:
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
                items=[("OIL-SYN", 8000), ("FIL-OIL", 1299)],
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
            ev(
                "o3",
                EventType.LINE_ITEM_ADDED,
                at(10, 2),
                "t_open",
                sku="WIP-STD",
                description="Wiper blades",
                qty=1,
                unit_cents=2199,
            ),
        ]
        rows = {row.family: row for row in by_family(events)}
        self.assertEqual(set(rows), {"oil", "oil+filter", "oil+wiper"})

        oil = rows["oil"]
        self.assertEqual(oil.tickets, 1)
        self.assertEqual(oil.closed, 1)
        self.assertEqual(oil.open, 0)
        self.assertEqual(oil.revenue_cents, 4000)
        self.assertAlmostEqual(oil.dwell_p50_min or 0.0, 30.0)

        mix = rows["oil+filter"]
        self.assertEqual(mix.tickets, 1)
        self.assertEqual(mix.closed, 1)
        self.assertEqual(mix.open, 0)
        self.assertEqual(mix.revenue_cents, 9299)
        self.assertAlmostEqual(mix.dwell_p50_min or 0.0, 60.0)

        open_row = rows["oil+wiper"]
        self.assertEqual(open_row.tickets, 1)
        self.assertEqual(open_row.closed, 0)
        self.assertEqual(open_row.open, 1)
        self.assertEqual(open_row.revenue_cents, 0)
        self.assertIsNone(open_row.dwell_p50_min)

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
            ev(
                "e3",
                EventType.LINE_ITEM_ADDED,
                at(8, 2),
                "t1",
                sku="FIL-OIL",
                description="Oil filter",
                qty=1,
                unit_cents=1299,
            ),
        ]
        rows = by_family(events)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].family, "oil+filter")
        self.assertEqual(rows[0].tickets, 1)
        self.assertEqual(rows[0].closed, 0)
        self.assertEqual(rows[0].open, 1)
        self.assertEqual(rows[0].revenue_cents, 0)
        self.assertIsNone(rows[0].dwell_p50_min)

    def test_empty_log_is_empty_list(self) -> None:
        self.assertEqual(by_family([]), [])

    def test_sorts_by_revenue_then_family(self) -> None:
        events = [
            *ticket_flow(
                "t_z", at(8), 1000, prefix="z", items=[("OIL-CONV", 1000)]
            ),
            *ticket_flow(
                "t_a", at(9), 1000, prefix="a", items=[("OIL-CONV", 1000)]
            ),
            *ticket_flow(
                "t_m",
                at(10),
                5000,
                prefix="m",
                items=[("OIL-FS", 5000), ("FIL-OIL", 1299)],
            ),
        ]
        self.assertEqual(
            [row.family for row in by_family(events)],
            ["oil+filter", "oil"],
        )
        self.assertEqual(by_family(events)[1].tickets, 2)

    def test_tie_breaks_alphabetically(self) -> None:
        events = [
            *ticket_flow(
                "t_c", at(8), 2000, prefix="c", items=[("FIL-OIL", 2000)]
            ),
            *ticket_flow(
                "t_s", at(9), 2000, prefix="s", items=[("OIL-CONV", 2000)]
            ),
        ]
        self.assertEqual(
            [row.family for row in by_family(events)],
            ["filter", "oil"],
        )

    def test_seeded_day_matches_manual_projection(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        rows = by_family(events)
        tickets = project(events)
        self.assertEqual(sum(row.tickets for row in rows), len(tickets))
        expected = sum(t.total_cents for t in tickets.values() if t.closed)
        self.assertEqual(sum(row.revenue_cents for row in rows), expected)
        self.assertGreater(expected, 0)
        leftover = sum(1 for t in tickets.values() if not t.closed)
        self.assertEqual(sum(row.open for row in rows), leftover)
        names = {row.family for row in rows}
        self.assertTrue({"oil+filter"} <= names)

        by: dict[str, list] = {}
        for ticket in tickets.values():
            by.setdefault(family_of(ticket.items), []).append(ticket)
        manual: list[FamilyRow] = []
        for family, group in by.items():
            closed = [t for t in group if t.closed]
            dwells = [
                (t.closed_at - t.opened_at).total_seconds() / 60.0
                for t in closed
                if t.closed_at is not None
            ]
            manual.append(
                FamilyRow(
                    family=family,
                    tickets=len(group),
                    closed=len(closed),
                    open=len(group) - len(closed),
                    revenue_cents=sum(t.total_cents for t in closed),
                    dwell_p50_min=percentile(dwells, 0.50),
                )
            )
        manual.sort(key=lambda row: (-row.revenue_cents, row.family))
        self.assertEqual(rows, manual)


class FamilyRenderTests(unittest.TestCase):
    def test_human_and_json_match_fold(self) -> None:
        events = [
            *ticket_flow(
                "t_a", at(8), 6999, prefix="a", items=[("OIL-SYN", 6999)]
            ),
            *ticket_flow(
                "t_b",
                at(9),
                1299,
                prefix="b",
                items=[("OIL-CONV", 3999), ("FIL-OIL", 1299)],
            ),
        ]
        rows = by_family(events)
        text = render_family(events, rows)
        self.assertIn("family", text)
        self.assertIn("2 families", text)
        self.assertIn("2 tickets", text)
        self.assertIn("oil", text)
        self.assertIn("oil+filter", text)
        payload = json.loads(render_family_json(events, rows))
        self.assertEqual(payload["events"], len(events))
        self.assertIn("families", payload)
        self.assertEqual(
            [(r["family"], r["tickets"], r["revenue_cents"]) for r in payload["families"]],
            [(row.family, row.tickets, row.revenue_cents) for row in rows],
        )
