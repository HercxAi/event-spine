from __future__ import annotations

import unittest
from datetime import timedelta

from event_spine.detect import detect
from event_spine.events import Event, EventType
from event_spine.simulate import SimConfig, simulate_day
from event_spine.stats import dwell_minutes, percentile, summarize
from tests.helpers import at, ev, ticket_flow


class PercentileTests(unittest.TestCase):
    def test_odd_sample_hand_computed(self) -> None:
        # [1, 2, 3, 4, 5]; n-1 = 4
        # p50 → idx 2 → 3
        # p95 → idx 3.8 → 4 + 0.8*(5-4) = 4.8
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        self.assertEqual(percentile(values, 0.50), 3.0)
        self.assertAlmostEqual(percentile(values, 0.95) or 0.0, 4.8)

    def test_even_sample_interpolates(self) -> None:
        # [10, 20, 30, 40]; n-1 = 3
        # p50 → idx 1.5 → 20 + 0.5*(30-20) = 25
        values = [40.0, 10.0, 30.0, 20.0]
        self.assertEqual(percentile(values, 0.50), 25.0)
        self.assertEqual(percentile(values, 0.0), 10.0)
        self.assertEqual(percentile(values, 1.0), 40.0)

    def test_empty_is_none_single_is_itself(self) -> None:
        self.assertIsNone(percentile([], 0.50))
        self.assertEqual(percentile([7.0], 0.95), 7.0)

    def test_rejects_p_outside_unit_interval(self) -> None:
        with self.assertRaises(ValueError):
            percentile([1.0], 1.5)
        with self.assertRaises(ValueError):
            percentile([1.0], -0.01)


class SummarizeTests(unittest.TestCase):
    def test_counts_and_dwell_from_known_tickets(self) -> None:
        events: list[Event] = []
        t = at(8)
        # 10 min, 20 min, 30 min, 40 min, 50 min dwells. One failed payment.
        for i, minutes in enumerate((10, 20, 30, 40, 50)):
            events.extend(
                ticket_flow(
                    f"t_{i}",
                    t,
                    7000,
                    prefix=f"n{i}",
                    fail=(i == 0),
                    dwell=timedelta(minutes=minutes),
                )
            )
            t += timedelta(hours=1)
        stats = summarize(events)
        self.assertEqual(stats.tickets, 5)
        self.assertEqual(stats.closed, 5)
        self.assertEqual(stats.failures, 1)
        self.assertEqual(stats.payments, 6)  # one fail + five captures
        self.assertAlmostEqual(stats.fail_rate, 1 / 6)
        # p50 of [10,20,30,40,50] = 30; p95 → idx 3.8 → 40 + 0.8*10 = 48
        self.assertAlmostEqual(stats.dwell_p50_min or 0.0, 30.0)
        self.assertAlmostEqual(stats.dwell_p95_min or 0.0, 48.0)
        hits = dict(stats.detector_hits)
        self.assertEqual(hits["ticket_dwell"], 0)
        self.assertEqual(hits["ticket_total"], 0)
        self.assertIn("payment_failure", hits)
        self.assertIn("velocity", hits)

    def test_open_ticket_is_counted_but_not_in_dwell(self) -> None:
        events = [
            ev("e1", EventType.TICKET_OPENED, at(8), "t_open", bay="1", vehicle="x"),
            *ticket_flow("t_done", at(9), 7000, prefix="c", dwell=timedelta(minutes=12)),
        ]
        stats = summarize(events)
        self.assertEqual(stats.tickets, 2)
        self.assertEqual(stats.closed, 1)
        self.assertEqual(dwell_minutes(events), [12.0])
        self.assertAlmostEqual(stats.dwell_p50_min or 0.0, 12.0)
        self.assertAlmostEqual(stats.dwell_p95_min or 0.0, 12.0)

    def test_empty_log_is_zeros(self) -> None:
        stats = summarize([])
        self.assertEqual(stats.events, 0)
        self.assertEqual(stats.tickets, 0)
        self.assertEqual(stats.fail_rate, 0.0)
        self.assertIsNone(stats.dwell_p50_min)
        self.assertIsNone(stats.dwell_p95_min)
        self.assertEqual(sum(n for _, n in stats.detector_hits), 0)

    def test_seeded_day_matches_detectors_and_p95_beats_p50(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        stats = summarize(events)
        self.assertGreaterEqual(stats.tickets, 20)
        self.assertEqual(stats.tickets, sum(1 for e in events if e.type is EventType.TICKET_OPENED))
        self.assertGreater(stats.fail_rate, 0.0)
        self.assertLess(stats.fail_rate, 1.0)
        assert stats.dwell_p50_min is not None
        assert stats.dwell_p95_min is not None
        self.assertGreaterEqual(stats.dwell_p95_min, stats.dwell_p50_min)
        # One 3-hour plant is the max; p95 still describes the oil-change shop.
        dwells = dwell_minutes(events)
        self.assertGreater(max(dwells), 180.0)
        self.assertLess(stats.dwell_p95_min, 15.0)
        by_name = dict(stats.detector_hits)
        counted = detect(events)
        for name, n in by_name.items():
            self.assertEqual(n, sum(1 for a in counted if a.detector == name))
        self.assertGreater(by_name["ticket_dwell"], 0)
        self.assertGreater(by_name["ticket_total"], 0)
        self.assertGreater(by_name["payment_failure"], 0)
        self.assertGreater(by_name["payment_failure_cusum"], 0)
        self.assertGreater(by_name["velocity"], 0)
        self.assertGreater(by_name["concurrent_open"], 0)
