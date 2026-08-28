from __future__ import annotations

import json
import unittest

from event_spine.events import EventType
from event_spine.family import by_family, family_of
from event_spine.report import render_family, render_family_json
from event_spine.simulate import SimConfig, simulate_day
from tests.helpers import at, ev, ticket_flow


class FamilyOfTests(unittest.TestCase):
    def test_prefix_bare_blank_and_leading_hyphen(self) -> None:
        self.assertEqual(family_of("OIL-CONV"), "OIL")
        self.assertEqual(family_of("INSP"), "INSP")
        self.assertEqual(family_of(""), "?")
        self.assertEqual(family_of("-FOO"), "?")


class FamilyFoldTests(unittest.TestCase):
    def test_aggregates_across_tickets_via_ticket_flow(self) -> None:
        events = [
            *ticket_flow("t_a", at(8), 4000, prefix="a", items=[("OIL-CONV", 4000)]),
            *ticket_flow("t_b", at(9), 8000, prefix="b", items=[("OIL-CONV", 4000), ("OIL-SYN", 4000)]),
            *ticket_flow("t_c", at(10), 1299, prefix="c", items=[("FIL-OIL", 1299)]),
        ]
        rows = {row.family: row for row in by_family(events)}
        self.assertEqual(rows["OIL"].lines, 3)
        self.assertEqual(rows["OIL"].skus, 2)
        self.assertEqual(rows["OIL"].qty, 3)
        self.assertEqual(rows["OIL"].ext_cents, 12000)
        self.assertEqual(rows["FIL"].lines, 1)
        self.assertEqual(rows["FIL"].skus, 1)
        self.assertEqual(rows["FIL"].ext_cents, 1299)

    def test_line_item_events_drive_fold(self) -> None:
        events = [
            ev("e1", EventType.TICKET_OPENED, at(8), "t1", bay="1", vehicle="x"),
            ev(
                "e2",
                EventType.LINE_ITEM_ADDED,
                at(8, 1),
                "t1",
                sku="OIL-CONV",
                description="Conventional 5W-30",
                qty=1,
                unit_cents=3999,
            ),
            ev(
                "e3",
                EventType.LINE_ITEM_ADDED,
                at(8, 2),
                "t1",
                sku="OIL-CONV",
                description="Conventional 5W-30",
                qty=2,
                unit_cents=3999,
            ),
            ev(
                "e4",
                EventType.LINE_ITEM_ADDED,
                at(8, 3),
                "t1",
                sku="FIL-OIL",
                description="Oil filter",
                qty=1,
                unit_cents=1299,
            ),
            ev(
                "e5",
                EventType.LINE_ITEM_ADDED,
                at(8, 4),
                "t1",
                sku="INSP",
                description="Multi-point inspection",
                qty=1,
                unit_cents=0,
            ),
            ev(
                "e6",
                EventType.PAYMENT_CAPTURED,
                at(8, 5),
                "t1",
                method="card",
                amount_cents=13296,
            ),
            ev("e7", EventType.TICKET_CLOSED, at(8, 6), "t1", total_cents=13296),
        ]
        rows = by_family(events)
        self.assertEqual([r.family for r in rows], ["OIL", "FIL", "INSP"])
        oil = rows[0]
        self.assertEqual(oil.skus, 1)
        self.assertEqual(oil.lines, 2)
        self.assertEqual(oil.qty, 3)
        self.assertEqual(oil.ext_cents, 3 * 3999)
        filt = rows[1]
        self.assertEqual(filt.skus, 1)
        self.assertEqual(filt.lines, 1)
        self.assertEqual(filt.qty, 1)
        self.assertEqual(filt.ext_cents, 1299)
        insp = rows[2]
        self.assertEqual(insp.skus, 1)
        self.assertEqual(insp.qty, 1)
        self.assertEqual(insp.ext_cents, 0)

    def test_blank_and_leading_hyphen_sku_become_unknown(self) -> None:
        events = [
            ev(
                "e1",
                EventType.LINE_ITEM_ADDED,
                at(8),
                "t1",
                sku="",
                description="",
                qty=1,
                unit_cents=100,
            ),
            ev(
                "e2",
                EventType.LINE_ITEM_ADDED,
                at(8, 1),
                "t1",
                sku="-WEIRD",
                description="",
                qty=2,
                unit_cents=50,
            ),
        ]
        rows = by_family(events)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].family, "?")
        self.assertEqual(rows[0].skus, 2)
        self.assertEqual(rows[0].lines, 2)
        self.assertEqual(rows[0].qty, 3)
        self.assertEqual(rows[0].ext_cents, 200)

    def test_empty_log_is_empty_list(self) -> None:
        self.assertEqual(by_family([]), [])

    def test_ignores_non_line_item_events(self) -> None:
        events = [
            ev("e1", EventType.TICKET_OPENED, at(8), "t1", bay="1", vehicle="x"),
            ev(
                "e2",
                EventType.PAYMENT_FAILED,
                at(8, 1),
                "t1",
                method="card",
                amount_cents=100,
                reason="declined",
            ),
        ]
        self.assertEqual(by_family(events), [])

    def test_sorts_by_ext_then_family(self) -> None:
        events = [
            ev(
                "e1",
                EventType.LINE_ITEM_ADDED,
                at(8),
                "t1",
                sku="ZZZ-1",
                description="z",
                qty=1,
                unit_cents=1000,
            ),
            ev(
                "e2",
                EventType.LINE_ITEM_ADDED,
                at(8, 1),
                "t1",
                sku="AAA-1",
                description="a",
                qty=1,
                unit_cents=1000,
            ),
            ev(
                "e3",
                EventType.LINE_ITEM_ADDED,
                at(8, 2),
                "t1",
                sku="MID-1",
                description="m",
                qty=1,
                unit_cents=5000,
            ),
        ]
        self.assertEqual([r.family for r in by_family(events)], ["MID", "AAA", "ZZZ"])

    def test_seeded_day_covers_menu_and_whale(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        rows = by_family(events)
        by_code = {row.family: row for row in rows}
        self.assertIn("OIL", by_code)
        self.assertIn("FIL", by_code)
        self.assertGreater(by_code["OIL"].qty, 0)
        self.assertGreater(by_code["FIL"].qty, 0)
        # Whale plant adds TRN-FLUSH / DIFF-FLUID / BRK-FLUSH once each.
        self.assertIn("TRN", by_code)
        self.assertEqual(by_code["TRN"].qty, 1)
        self.assertEqual(by_code["TRN"].ext_cents, 18900)
        # Ext sum matches every LineItemAdded.
        expected = 0
        for event in events:
            if event.type is EventType.LINE_ITEM_ADDED:
                expected += int(event.payload.get("qty", 1)) * int(event.payload["unit_cents"])
        self.assertEqual(sum(row.ext_cents for row in rows), expected)


class FamilyRenderTests(unittest.TestCase):
    def test_human_and_json_match_fold(self) -> None:
        events = [
            ev(
                "e1",
                EventType.LINE_ITEM_ADDED,
                at(8),
                "t1",
                sku="OIL-SYN",
                description="Synthetic 5W-30",
                qty=1,
                unit_cents=6999,
            ),
            ev(
                "e2",
                EventType.LINE_ITEM_ADDED,
                at(8, 1),
                "t1",
                sku="FIL-OIL",
                description="Oil filter",
                qty=1,
                unit_cents=1299,
            ),
        ]
        rows = by_family(events)
        text = render_family(events, rows)
        self.assertIn("family", text)
        self.assertIn("OIL", text)
        self.assertIn("FIL", text)
        self.assertIn("2 families", text)
        self.assertIn("$69.99", text)
        payload = json.loads(render_family_json(events, rows))
        self.assertEqual(payload["events"], 2)
        self.assertEqual(
            [(r["family"], r["skus"], r["qty"], r["ext_cents"]) for r in payload["families"]],
            [(row.family, row.skus, row.qty, row.ext_cents) for row in rows],
        )
