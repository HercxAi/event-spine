from __future__ import annotations

import unittest
from datetime import timedelta

from event_spine.brief import from_log
from event_spine.detect import detect, fmt_cents
from event_spine.events import Event, EventType
from event_spine.report import render_brief
from event_spine.simulate import SimConfig, simulate_day
from event_spine.stats import DETECTORS, summarize
from tests.helpers import at, ev, ticket_flow


class BriefFoldTests(unittest.TestCase):
    def test_counts_opens_closes_and_leftover(self) -> None:
        events = [
            *ticket_flow("t_done", at(8), 4000, prefix="a"),
            ev("o1", EventType.TICKET_OPENED, at(9), "t_open", bay="2", vehicle="x"),
            ev(
                "o2",
                EventType.LINE_ITEM_ADDED,
                at(9, 1),
                "t_open",
                sku="OIL-CONV",
                description="oil",
                qty=1,
                unit_cents=3999,
            ),
        ]
        brief = from_log(events)
        self.assertEqual(brief.tickets_opened, 2)
        self.assertEqual(brief.tickets_closed, 1)
        self.assertEqual(len(brief.leftover), 1)
        self.assertEqual(brief.leftover[0].ticket_id, "t_open")
        self.assertEqual(brief.leftover[0].bay, "2")
        self.assertFalse(brief.leftover[0].closed)

    def test_failed_payment_does_not_add_revenue(self) -> None:
        events = ticket_flow("t_fail", at(10), 7500, prefix="f", fail=True)
        brief = from_log(events)
        self.assertEqual(brief.payments_failed, 1)
        self.assertEqual(brief.payments_captured, 1)
        self.assertEqual(brief.revenue_cents, 7500)

    def test_revenue_is_captured_amount_cents_not_line_total(self) -> None:
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
                EventType.PAYMENT_CAPTURED,
                at(8, 2),
                "t1",
                method="card",
                amount_cents=2500,
            ),
            ev("e4", EventType.TICKET_CLOSED, at(8, 3), "t1", total_cents=4000),
        ]
        brief = from_log(events)
        self.assertEqual(brief.revenue_cents, 2500)
        self.assertEqual(brief.tickets_closed, 1)

    def test_empty_log_is_zeros(self) -> None:
        brief = from_log([])
        self.assertEqual(brief.events, 0)
        self.assertEqual(brief.tickets_opened, 0)
        self.assertEqual(brief.tickets_closed, 0)
        self.assertEqual(brief.payments_captured, 0)
        self.assertEqual(brief.payments_failed, 0)
        self.assertEqual(brief.revenue_cents, 0)
        self.assertEqual(brief.leftover, ())
        self.assertEqual(sum(n for _, n in brief.detector_hits), 0)
        self.assertEqual([name for name, _ in brief.detector_hits], list(DETECTORS))

    def test_detector_hits_reuse_existing_stats_fold(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        brief = from_log(events)
        stats = summarize(events)
        counted = detect(events)
        self.assertEqual(brief.detector_hits, stats.detector_hits)
        by_name = dict(brief.detector_hits)
        for name, n in by_name.items():
            self.assertEqual(n, sum(1 for a in counted if a.detector == name))
        self.assertGreater(by_name["payment_failure_cusum"], 0)
        self.assertGreater(by_name["ticket_dwell"], 0)

    def test_seeded_day_has_leftover_and_captured_revenue(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        brief = from_log(events)
        self.assertEqual(
            brief.tickets_opened,
            sum(1 for e in events if e.type is EventType.TICKET_OPENED),
        )
        self.assertEqual(
            brief.tickets_closed,
            sum(1 for e in events if e.type is EventType.TICKET_CLOSED),
        )
        self.assertEqual(
            brief.payments_captured,
            sum(1 for e in events if e.type is EventType.PAYMENT_CAPTURED),
        )
        self.assertEqual(
            brief.payments_failed,
            sum(1 for e in events if e.type is EventType.PAYMENT_FAILED),
        )
        captured_cents = sum(
            int(e.payload["amount_cents"])
            for e in events
            if e.type is EventType.PAYMENT_CAPTURED
        )
        self.assertEqual(brief.revenue_cents, captured_cents)
        self.assertGreater(brief.tickets_opened, brief.tickets_closed)
        self.assertEqual(len(brief.leftover), brief.tickets_opened - brief.tickets_closed)
        self.assertGreater(len(brief.leftover), 0)
        self.assertTrue(all(not ticket.closed for ticket in brief.leftover))

    def test_fold_does_not_mutate_events(self) -> None:
        events = ticket_flow("t_1", at(8), 4000, prefix="m", dwell=timedelta(minutes=12))
        before = [e.to_dict() for e in events]
        from_log(events)
        self.assertEqual([e.to_dict() for e in events], before)

    def test_render_empty_log_is_readable(self) -> None:
        text = render_brief([])
        self.assertIn("daily brief", text)
        self.assertIn("opened 0", text)
        self.assertIn("closed 0", text)
        self.assertIn("leftover open 0", text)
        self.assertIn("captured 0", text)
        self.assertIn("failed 0", text)
        self.assertIn(f"revenue {fmt_cents(0)}", text)
        self.assertIn("detector hits", text)
        self.assertIn("ticket_total", text)
        self.assertTrue(text.endswith("\n"))

    def test_render_lists_leftover_and_dollar_revenue(self) -> None:
        events = [
            *ticket_flow("t_done", at(8), 12997, prefix="r"),
            ev("o1", EventType.TICKET_OPENED, at(16, 3), "t_left", bay="3", vehicle="x"),
        ]
        text = render_brief(events)
        self.assertIn("opened 2", text)
        self.assertIn("closed 1", text)
        self.assertIn("leftover open 1", text)
        self.assertIn("t_left", text)
        self.assertIn("16:03", text)
        self.assertIn("bay 3", text)
        self.assertIn(f"revenue {fmt_cents(12997)}", text)
        self.assertIn("daily brief", text)
