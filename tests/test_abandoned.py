from __future__ import annotations

import unittest
from datetime import timedelta

from event_spine.detect import detect, detect_declined_abandoned
from event_spine.events import Event, EventType
from event_spine.report import render_abandoned
from event_spine.simulate import SimConfig, simulate_day
from tests.helpers import at, ev, ticket_flow


class DeclinedAbandonedDetectorTests(unittest.TestCase):
    def test_flags_declined_ticket_left_open(self) -> None:
        events = ticket_flow("t_walk", at(17, 22), 8298, prefix="w", abandon=True)
        hits = detect_declined_abandoned(events)
        self.assertEqual(len(hits), 1)
        hit = hits[0]
        self.assertEqual(hit.detector, "declined_abandoned")
        self.assertEqual(hit.ticket_id, "t_walk")
        self.assertAlmostEqual(hit.score, 82.98)
        self.assertTrue(hit.details["open"])
        self.assertEqual(hit.details["failed_payments"], 1)
        self.assertIn("still open", hit.summary)
        self.assertTrue(any(eid.startswith("w") for eid in hit.event_ids))

    def test_flags_closed_after_fail_without_capture(self) -> None:
        events = ticket_flow(
            "t_void",
            at(11, 5),
            5298,
            prefix="v",
            close_unpaid=True,
        )
        hits = detect_declined_abandoned(events)
        self.assertEqual(len(hits), 1)
        hit = hits[0]
        self.assertEqual(hit.ticket_id, "t_void")
        self.assertFalse(hit.details["open"])
        self.assertIn("closed without capture", hit.summary)
        self.assertTrue(any(e.type is EventType.TICKET_CLOSED for e in events))
        self.assertFalse(any(e.type is EventType.PAYMENT_CAPTURED for e in events))

    def test_fail_then_capture_does_not_flag(self) -> None:
        events = ticket_flow("t_retry", at(10, 15), 7000, prefix="r", fail=True)
        self.assertTrue(any(e.type is EventType.PAYMENT_FAILED for e in events))
        self.assertTrue(any(e.type is EventType.PAYMENT_CAPTURED for e in events))
        self.assertEqual(detect_declined_abandoned(events), [])

    def test_capture_after_two_fails_does_not_flag(self) -> None:
        events = [
            ev("a01", EventType.TICKET_OPENED, at(16, 3), "t_out", bay="2", vehicle="x"),
            ev(
                "a02",
                EventType.LINE_ITEM_ADDED,
                at(16, 4),
                "t_out",
                sku="OIL-SYN",
                unit_cents=6999,
                qty=1,
            ),
            ev(
                "a03",
                EventType.PAYMENT_FAILED,
                at(16, 5),
                "t_out",
                method="card",
                amount_cents=6999,
                reason="network",
            ),
            ev(
                "a04",
                EventType.PAYMENT_FAILED,
                at(16, 5, 20),
                "t_out",
                method="card",
                amount_cents=6999,
                reason="network",
            ),
            ev(
                "a05",
                EventType.PAYMENT_CAPTURED,
                at(16, 5, 40),
                "t_out",
                method="card",
                amount_cents=6999,
            ),
            ev("a06", EventType.TICKET_CLOSED, at(16, 6), "t_out", total_cents=6999),
        ]
        self.assertEqual(detect_declined_abandoned(events), [])

    def test_later_fail_without_capture_flags_even_after_earlier_capture(self) -> None:
        events = [
            *ticket_flow("t_ok", at(9), 4000, prefix="ok"),
            ev("b01", EventType.TICKET_OPENED, at(12), "t_split", bay="1", vehicle="x"),
            ev(
                "b02",
                EventType.LINE_ITEM_ADDED,
                at(12, 1),
                "t_split",
                sku="OIL-CONV",
                unit_cents=3999,
                qty=1,
            ),
            ev(
                "b03",
                EventType.PAYMENT_CAPTURED,
                at(12, 2),
                "t_split",
                method="card",
                amount_cents=2000,
            ),
            ev(
                "b04",
                EventType.PAYMENT_FAILED,
                at(12, 3),
                "t_split",
                method="card",
                amount_cents=1999,
                reason="declined",
            ),
        ]
        hits = detect_declined_abandoned(events)
        self.assertEqual([a.ticket_id for a in hits], ["t_split"])

    def test_empty_log_and_paid_tickets_are_quiet(self) -> None:
        self.assertEqual(detect_declined_abandoned([]), [])
        events = ticket_flow("t_1", at(8), 7000, prefix="n")
        self.assertEqual(detect_declined_abandoned(events), [])
        self.assertEqual(detect([]), [])

    def test_does_not_mutate_events(self) -> None:
        events = ticket_flow("t_walk", at(17, 22), 7000, prefix="w", abandon=True)
        before = [e.to_dict() for e in events]
        detect_declined_abandoned(events)
        self.assertEqual([e.to_dict() for e in events], before)

    def test_seeded_day_flags_walkoff_not_retries(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        hits = detect_declined_abandoned(events)
        self.assertGreaterEqual(len(hits), 1)
        walkoffs = [a for a in hits if a.at.hour == 17 and a.at.minute >= 22]
        self.assertEqual(len(walkoffs), 1)
        plant = walkoffs[0]
        self.assertEqual(plant.detector, "declined_abandoned")
        self.assertTrue(plant.details["open"])
        self.assertEqual(plant.details["reason"], "declined")
        self.assertGreater(plant.details["total_cents"], 0)
        # Every flagged ticket has a fail and no later capture.
        by_ticket: dict[str, list[Event]] = {}
        for event in events:
            by_ticket.setdefault(event.ticket_id, []).append(event)
        for hit in hits:
            assert hit.ticket_id is not None
            rows = sorted(
                by_ticket[hit.ticket_id],
                key=lambda e: (e.occurred_at, e.event_id),
            )
            last_fail = None
            captured_after = False
            for event in rows:
                if event.type is EventType.PAYMENT_FAILED:
                    last_fail = event
                    captured_after = False
                elif event.type is EventType.PAYMENT_CAPTURED and last_fail is not None:
                    captured_after = True
            self.assertIsNotNone(last_fail)
            self.assertFalse(captured_after)
        # Organic fail-then-capture tickets stay off the list.
        retry_ids = set()
        for ticket_id, rows in by_ticket.items():
            rows = sorted(rows, key=lambda e: (e.occurred_at, e.event_id))
            failed = False
            captured_after = False
            for event in rows:
                if event.type is EventType.PAYMENT_FAILED:
                    failed = True
                    captured_after = False
                elif event.type is EventType.PAYMENT_CAPTURED and failed:
                    captured_after = True
            if failed and captured_after:
                retry_ids.add(ticket_id)
        self.assertTrue(retry_ids)
        flagged = {a.ticket_id for a in hits}
        self.assertFalse(retry_ids & flagged)
        names = {a.detector for a in detect(events)}
        self.assertIn("declined_abandoned", names)

    def test_detect_includes_closed_unpaid(self) -> None:
        events = [
            *ticket_flow("t_ok", at(8), 7000, prefix="n"),
            *ticket_flow("t_void", at(11), 7000, prefix="v", close_unpaid=True),
        ]
        names = {a.detector for a in detect(events)}
        self.assertIn("declined_abandoned", names)

    def test_render_mentions_walkoff(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        text = render_abandoned(events)
        self.assertIn("declined abandoned", text)
        self.assertIn("PaymentFailed", text)
        self.assertIn("walked", text)
        self.assertIn("events:", text)
        self.assertNotIn("no declined-abandoned tickets", text)

    def test_render_quiet_day(self) -> None:
        events: list[Event] = []
        t = at(8)
        for i in range(4):
            events.extend(
                ticket_flow(
                    f"t_{i}",
                    t,
                    7000,
                    prefix=f"n{i}",
                    fail=(i == 0),
                    dwell=timedelta(minutes=8),
                )
            )
            t += timedelta(hours=1)
        text = render_abandoned(events)
        self.assertIn("no declined-abandoned tickets", text)
        self.assertIn("declined abandoned", text)
