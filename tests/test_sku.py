from __future__ import annotations

import json
import unittest

from event_spine.events import Event, EventType
from event_spine.report import render_sku, render_sku_json
from event_spine.simulate import SimConfig, simulate_day
from event_spine.sku import by_sku
from tests.helpers import at, ev, ticket_flow


class SkuFoldTests(unittest.TestCase):
    def test_aggregates_across_tickets_via_ticket_flow(self) -> None:
        events = [
            *ticket_flow("t_a", at(8), 4000, prefix="a", items=[("OIL-CONV", 4000)]),
            *ticket_flow("t_b", at(9), 8000, prefix="b", items=[("OIL-CONV", 4000), ("OIL-CONV", 4000)]),
            *ticket_flow("t_c", at(10), 1299, prefix="c", items=[("FIL-OIL", 1299)]),
        ]
        rows = {row.sku: row for row in by_sku(events)}
        self.assertEqual(rows["OIL-CONV"].lines, 3)
        self.assertEqual(rows["OIL-CONV"].qty, 3)
        self.assertEqual(rows["OIL-CONV"].ext_cents, 12000)
        self.assertEqual(rows["FIL-OIL"].lines, 1)
        self.assertEqual(rows["FIL-OIL"].ext_cents, 1299)

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
                EventType.PAYMENT_CAPTURED,
                at(8, 4),
                "t1",
                method="card",
                amount_cents=13296,
            ),
            ev("e6", EventType.TICKET_CLOSED, at(8, 5), "t1", total_cents=13296),
        ]
        rows = by_sku(events)
        self.assertEqual([r.sku for r in rows], ["OIL-CONV", "FIL-OIL"])
        oil = rows[0]
        self.assertEqual(oil.lines, 2)
        self.assertEqual(oil.qty, 3)
        self.assertEqual(oil.ext_cents, 3 * 3999)
        self.assertEqual(oil.description, "Conventional 5W-30")
        filt = rows[1]
        self.assertEqual(filt.lines, 1)
        self.assertEqual(filt.qty, 1)
        self.assertEqual(filt.ext_cents, 1299)

    def test_empty_log_is_empty_list(self) -> None:
        self.assertEqual(by_sku([]), [])

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
        self.assertEqual(by_sku(events), [])

    def test_sorts_by_ext_then_sku(self) -> None:
        events = [
            ev(
                "e1",
                EventType.LINE_ITEM_ADDED,
                at(8),
                "t1",
                sku="ZZZ",
                description="z",
                qty=1,
                unit_cents=1000,
            ),
            ev(
                "e2",
                EventType.LINE_ITEM_ADDED,
                at(8, 1),
                "t1",
                sku="AAA",
                description="a",
                qty=1,
                unit_cents=1000,
            ),
            ev(
                "e3",
                EventType.LINE_ITEM_ADDED,
                at(8, 2),
                "t1",
                sku="MID",
                description="m",
                qty=1,
                unit_cents=5000,
            ),
        ]
        self.assertEqual([r.sku for r in by_sku(events)], ["MID", "AAA", "ZZZ"])

    def test_seeded_day_covers_menu_and_whale(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        rows = by_sku(events)
        by_code = {row.sku: row for row in rows}
        self.assertIn("FIL-OIL", by_code)
        self.assertGreater(by_code["FIL-OIL"].qty, 0)
        # Whale plant adds TRN-FLUSH / DIFF-FLUID / BRK-FLUSH once each.
        self.assertIn("TRN-FLUSH", by_code)
        self.assertEqual(by_code["TRN-FLUSH"].qty, 1)
        self.assertEqual(by_code["TRN-FLUSH"].ext_cents, 18900)
        # Ext sum matches every LineItemAdded.
        expected = 0
        for event in events:
            if event.type is EventType.LINE_ITEM_ADDED:
                expected += int(event.payload.get("qty", 1)) * int(event.payload["unit_cents"])
        self.assertEqual(sum(row.ext_cents for row in rows), expected)


class SkuRenderTests(unittest.TestCase):
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
        rows = by_sku(events)
        text = render_sku(events, rows)
        self.assertIn("sku", text)
        self.assertIn("OIL-SYN", text)
        self.assertIn("FIL-OIL", text)
        self.assertIn("2 skus", text)
        payload = json.loads(render_sku_json(events, rows))
        self.assertEqual(payload["events"], 2)
        self.assertEqual(
            [(r["sku"], r["qty"], r["ext_cents"]) for r in payload["skus"]],
            [(row.sku, row.qty, row.ext_cents) for row in rows],
        )
