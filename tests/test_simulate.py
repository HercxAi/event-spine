from __future__ import annotations

import unittest
from collections import Counter

from event_spine.detect import detect
from event_spine.events import EventType
from event_spine.project import project
from event_spine.simulate import SimConfig, simulate_day


class SimulateTests(unittest.TestCase):
    def test_seed_is_deterministic(self) -> None:
        a = simulate_day(SimConfig(seed=42))
        b = simulate_day(SimConfig(seed=42))
        self.assertEqual([e.event_id for e in a], [e.event_id for e in b])
        self.assertEqual([e.to_dict() for e in a], [e.to_dict() for e in b])

    def test_different_seeds_differ(self) -> None:
        a = simulate_day(SimConfig(seed=1))
        b = simulate_day(SimConfig(seed=2))
        self.assertNotEqual([e.to_dict() for e in a], [e.to_dict() for e in b])

    def test_emits_all_five_event_types(self) -> None:
        types = {e.type for e in simulate_day(SimConfig(seed=42))}
        self.assertEqual(types, set(EventType))

    def test_dozens_to_hundreds_of_events(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        self.assertGreaterEqual(len(events), 80)
        self.assertLess(len(events), 2000)
        opened = sum(1 for e in events if e.type is EventType.TICKET_OPENED)
        self.assertGreaterEqual(opened, 20)

    def test_events_are_time_ordered(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        times = [e.occurred_at for e in events]
        self.assertEqual(times, sorted(times))

    def test_closed_total_matches_line_items(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        tickets = project(events)
        for ticket in tickets.values():
            if not ticket.closed:
                continue
            closes = [
                e
                for e in events
                if e.ticket_id == ticket.ticket_id and e.type is EventType.TICKET_CLOSED
            ]
            self.assertEqual(closes[-1].payload["total_cents"], ticket.total_cents)

    def test_planted_irregularities_are_detected(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        anomalies = detect(events)
        names = {a.detector for a in anomalies}
        self.assertIn("ticket_total", names)
        self.assertIn("ticket_total_mad", names)
        self.assertIn("payment_failure", names)
        self.assertIn("payment_failure_cusum", names)
        self.assertIn("payment_failure_ewma", names)
        self.assertIn("velocity", names)
        self.assertIn("ticket_dwell", names)
        self.assertIn("concurrent_open", names)
        for anomaly in anomalies:
            self.assertGreater(len(anomaly.event_ids), 0)
            self.assertGreaterEqual(anomaly.score, 2.5)
        open_ids = {tid for tid, t in project(events).items() if not t.closed}
        self.assertTrue(open_ids)
        dwell_ids = {a.ticket_id for a in anomalies if a.detector == "ticket_dwell"}
        self.assertTrue(open_ids & dwell_ids)

    def test_type_counts_are_sane(self) -> None:
        counts = Counter(e.type for e in simulate_day(SimConfig(seed=42)))
        self.assertGreaterEqual(counts[EventType.LINE_ITEM_ADDED], counts[EventType.TICKET_OPENED])
        self.assertGreaterEqual(counts[EventType.PAYMENT_CAPTURED], 15)
        self.assertGreaterEqual(counts[EventType.PAYMENT_FAILED], 4)
