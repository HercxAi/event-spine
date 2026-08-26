from __future__ import annotations

import json
import unittest

from event_spine.detect import fmt_cents
from event_spine.events import Event, EventType
from event_spine.hours import SHOP_CLOSE_HOUR, SHOP_OPEN_HOUR, HourBin, by_hour
from event_spine.report import render_hours, render_hours_json
from event_spine.simulate import SimConfig, simulate_day
from tests.helpers import at, ev, ticket_flow


def _by_clock(events: list[Event]) -> dict[int, HourBin]:
    return {row.hour.hour: row for row in by_hour(events)}


class HourFoldTests(unittest.TestCase):
    def test_counts_follow_event_hour_not_ticket_open(self) -> None:
        events = [
            ev("e1", EventType.TICKET_OPENED, at(8), "t1", bay="1", vehicle="x"),
            ev(
                "e2",
                EventType.LINE_ITEM_ADDED,
                at(8, 1),
                "t1",
                sku="OIL-CONV",
                description="oil",
                qty=1,
                unit_cents=4000,
            ),
            ev(
                "e3",
                EventType.PAYMENT_FAILED,
                at(9, 5),
                "t1",
                method="card",
                amount_cents=4000,
                reason="declined",
            ),
            ev(
                "e4",
                EventType.PAYMENT_CAPTURED,
                at(9, 6),
                "t1",
                method="card",
                amount_cents=4000,
            ),
            ev("e5", EventType.TICKET_CLOSED, at(9, 7), "t1", total_cents=4000),
        ]
        bins = _by_clock(events)
        eight = bins[8]
        nine = bins[9]
        self.assertEqual(eight.tickets_opened, 1)
        self.assertEqual(eight.payments_captured, 0)
        self.assertEqual(eight.payments_failed, 0)
        self.assertEqual(eight.revenue_cents, 0)
        self.assertEqual(nine.tickets_opened, 0)
        self.assertEqual(nine.payments_captured, 1)
        self.assertEqual(nine.payments_failed, 1)
        self.assertEqual(nine.revenue_cents, 4000)

    def test_failed_payment_does_not_add_revenue(self) -> None:
        events = ticket_flow("t_fail", at(10), 7500, prefix="f", fail=True)
        row = _by_clock(events)[10]
        self.assertEqual(row.payments_failed, 1)
        self.assertEqual(row.payments_captured, 1)
        self.assertEqual(row.revenue_cents, 7500)

    def test_quiet_shop_hour_is_kept_as_zeros(self) -> None:
        events = [
            *ticket_flow("t_a", at(8), 4000, prefix="a"),
            *ticket_flow("t_b", at(10), 5500, prefix="b"),
        ]
        bins = by_hour(events)
        hours = [row.hour.hour for row in bins]
        self.assertEqual(hours[:12], list(range(SHOP_OPEN_HOUR, SHOP_CLOSE_HOUR)))
        nine = next(row for row in bins if row.hour.hour == 9)
        self.assertEqual(nine.tickets_opened, 0)
        self.assertEqual(nine.payments_captured, 0)
        self.assertEqual(nine.payments_failed, 0)
        self.assertEqual(nine.revenue_cents, 0)
        self.assertEqual(nine.peak_open, 0)
        eight = next(row for row in bins if row.hour.hour == 8)
        ten = next(row for row in bins if row.hour.hour == 10)
        self.assertEqual(eight.tickets_opened, 1)
        self.assertEqual(eight.revenue_cents, 4000)
        self.assertEqual(ten.tickets_opened, 1)
        self.assertEqual(ten.revenue_cents, 5500)

    def test_empty_log_still_emits_shop_hours(self) -> None:
        bins = by_hour([])
        self.assertEqual(len(bins), SHOP_CLOSE_HOUR - SHOP_OPEN_HOUR)
        self.assertEqual(bins[0].hour.hour, SHOP_OPEN_HOUR)
        self.assertEqual(bins[-1].hour.hour, SHOP_CLOSE_HOUR - 1)
        for row in bins:
            self.assertEqual(row.tickets_opened, 0)
            self.assertEqual(row.payments_captured, 0)
            self.assertEqual(row.payments_failed, 0)
            self.assertEqual(row.revenue_cents, 0)
            self.assertEqual(row.peak_open, 0)

    def test_peak_open_counts_overlap_in_the_hour(self) -> None:
        events = [
            ev("a1", EventType.TICKET_OPENED, at(8, 0), "t_a", bay="1", vehicle="x"),
            ev("b1", EventType.TICKET_OPENED, at(8, 10), "t_b", bay="2", vehicle="y"),
            ev("a2", EventType.TICKET_CLOSED, at(8, 20), "t_a"),
            ev("b2", EventType.TICKET_CLOSED, at(8, 30), "t_b"),
        ]
        self.assertEqual(_by_clock(events)[8].peak_open, 2)

    def test_peak_open_carries_through_a_quiet_hour(self) -> None:
        events = [
            ev("e1", EventType.TICKET_OPENED, at(8, 50), "t_long", bay="1", vehicle="x"),
            ev("e2", EventType.TICKET_CLOSED, at(10, 10), "t_long"),
        ]
        bins = _by_clock(events)
        self.assertEqual(bins[8].tickets_opened, 1)
        self.assertEqual(bins[8].peak_open, 1)
        self.assertEqual(bins[9].tickets_opened, 0)
        self.assertEqual(bins[9].peak_open, 1)
        self.assertEqual(bins[10].tickets_opened, 0)
        self.assertEqual(bins[10].peak_open, 1)

    def test_late_event_adds_an_after_close_bin(self) -> None:
        events = [
            ev("e1", EventType.TICKET_OPENED, at(18, 50), "t_late", bay="1", vehicle="x"),
            ev(
                "e2",
                EventType.PAYMENT_CAPTURED,
                at(19, 5),
                "t_late",
                method="card",
                amount_cents=3000,
            ),
            ev("e3", EventType.TICKET_CLOSED, at(19, 6), "t_late", total_cents=3000),
        ]
        bins = by_hour(events)
        hours = [row.hour.hour for row in bins]
        self.assertIn(18, hours)
        self.assertIn(19, hours)
        self.assertEqual(hours.count(19), 1)
        nineteen = next(row for row in bins if row.hour.hour == 19)
        self.assertEqual(nineteen.payments_captured, 1)
        self.assertEqual(nineteen.revenue_cents, 3000)

    def test_seeded_day_partitions_the_log(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        bins = by_hour(events)
        shop = list(range(SHOP_OPEN_HOUR, SHOP_CLOSE_HOUR))
        self.assertEqual([row.hour.hour for row in bins[: len(shop)]], shop)
        self.assertEqual(
            sum(row.tickets_opened for row in bins),
            sum(1 for e in events if e.type is EventType.TICKET_OPENED),
        )
        self.assertEqual(
            sum(row.payments_captured for row in bins),
            sum(1 for e in events if e.type is EventType.PAYMENT_CAPTURED),
        )
        self.assertEqual(
            sum(row.payments_failed for row in bins),
            sum(1 for e in events if e.type is EventType.PAYMENT_FAILED),
        )
        captured_cents = sum(
            int(e.payload["amount_cents"])
            for e in events
            if e.type is EventType.PAYMENT_CAPTURED
        )
        self.assertEqual(sum(row.revenue_cents for row in bins), captured_cents)
        sixteen = next(row for row in bins if row.hour.hour == 16)
        self.assertGreater(sixteen.payments_failed, 0)
        eleven = next(row for row in bins if row.hour.hour == 11)
        self.assertGreaterEqual(eleven.tickets_opened, 8)

    def test_render_uses_dollar_label_from_integer_cents(self) -> None:
        events = ticket_flow("t_1", at(8), 12997, prefix="r")
        text = render_hours(events)
        self.assertIn("opened 1", text)
        self.assertIn("captured 1", text)
        self.assertIn("failed 0", text)
        self.assertIn(f"revenue {fmt_cents(12997)}", text)
        self.assertIn("07:00  opened 0", text)
        self.assertIn("09:00  opened 0", text)
        self.assertIn("hourly", text)

    def test_fold_does_not_mutate_events(self) -> None:
        events = ticket_flow("t_1", at(8), 4000, prefix="m")
        before = [e.to_dict() for e in events]
        by_hour(events)
        self.assertEqual([e.to_dict() for e in events], before)


class HoursJsonTests(unittest.TestCase):
    def test_render_hours_json_matches_fold(self) -> None:
        events = ticket_flow("t_1", at(8), 12997, prefix="r")
        bins = by_hour(events)
        payload = json.loads(render_hours_json(events, bins))
        self.assertEqual(payload["events"], len(events))
        self.assertEqual(len(payload["hours"]), len(bins))
        by_iso = {row["hour"]: row for row in payload["hours"]}
        for row in bins:
            got = by_iso[row.hour.isoformat()]
            self.assertEqual(got["tickets_opened"], row.tickets_opened)
            self.assertEqual(got["payments_captured"], row.payments_captured)
            self.assertEqual(got["payments_failed"], row.payments_failed)
            self.assertEqual(got["revenue_cents"], row.revenue_cents)
            self.assertEqual(got["peak_open"], row.peak_open)
        self.assertEqual(sum(r["revenue_cents"] for r in payload["hours"]), 12997)
