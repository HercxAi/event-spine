from __future__ import annotations

import math
import unittest
from datetime import timedelta

from event_spine.detect import (
    detect,
    detect_payment_failures,
    detect_ticket_dwell,
    detect_ticket_totals,
    detect_velocity,
    proportion_z,
    sample_mean_std,
    zscore,
)
from event_spine.events import Event, EventType
from tests.helpers import at, ev, ticket_flow


class MathTests(unittest.TestCase):
    def test_sample_std_hand_computed(self) -> None:
        # [10, 12, 11, 13, 10, 12, 11, 9]
        # mean = 11, ss = 12, s^2 = 12/7, s = sqrt(12/7)
        values = [10.0, 12.0, 11.0, 13.0, 10.0, 12.0, 11.0, 9.0]
        stats = sample_mean_std(values)
        assert stats is not None
        mean, std = stats
        self.assertAlmostEqual(mean, 11.0)
        self.assertAlmostEqual(std, math.sqrt(12 / 7))

    def test_zscore_hand_computed(self) -> None:
        baseline = [10.0, 12.0, 11.0, 13.0, 10.0, 12.0, 11.0, 9.0]
        z = zscore(20.0, baseline)
        assert z is not None
        self.assertAlmostEqual(z, (20 - 11) / math.sqrt(12 / 7))

    def test_zscore_zero_variance(self) -> None:
        self.assertEqual(zscore(5.0, [5.0, 5.0, 5.0]), 0.0)
        self.assertEqual(zscore(9.0, [5.0, 5.0, 5.0]), math.inf)

    def test_zscore_needs_two_samples(self) -> None:
        self.assertIsNone(zscore(10.0, [10.0]))

    def test_proportion_z_hand_computed(self) -> None:
        # p0=0.05, n=8, 6 failures → p=0.75
        # se = sqrt(0.05*0.95/8)
        z = proportion_z(6, 8, 0.05)
        assert z is not None
        self.assertAlmostEqual(z, (0.75 - 0.05) / math.sqrt(0.05 * 0.95 / 8))

    def test_proportion_z_rejects_bad_prior(self) -> None:
        self.assertIsNone(proportion_z(1, 10, 0.0))
        self.assertIsNone(proportion_z(1, 10, 1.0))
        self.assertIsNone(proportion_z(1, 0, 0.05))


class TicketTotalDetectorTests(unittest.TestCase):
    def test_flags_whale_against_oil_change_baseline(self) -> None:
        events: list[Event] = []
        t = at(8)
        for i in range(12):
            events.extend(
                ticket_flow(f"t_{i:02d}", t, 7000 + i * 50, prefix=f"n{i:02d}")
            )
            t += timedelta(minutes=12)
        events.extend(
            ticket_flow("t_whale", t, 55_000, prefix="w", items=[("TRN-FLUSH", 55_000)])
        )
        hits = detect_ticket_totals(events, window=16, min_samples=8, z_thresh=2.8)
        self.assertTrue(any(a.ticket_id == "t_whale" for a in hits))
        whale = next(a for a in hits if a.ticket_id == "t_whale")
        self.assertGreaterEqual(whale.score, 2.8)
        self.assertEqual(whale.event_ids[0].startswith("w"), True)

    def test_warmup_does_not_flag_first_tickets(self) -> None:
        events: list[Event] = []
        t = at(8)
        # First ticket is huge; no baseline yet.
        events.extend(ticket_flow("t_big", t, 80_000, prefix="b"))
        t += timedelta(minutes=10)
        for i in range(6):
            events.extend(ticket_flow(f"t_{i}", t, 7000, prefix=f"n{i}"))
            t += timedelta(minutes=10)
        hits = detect_ticket_totals(events, min_samples=8)
        self.assertEqual(hits, [])


class PaymentDetectorTests(unittest.TestCase):
    def test_flags_afternoon_failure_burst(self) -> None:
        events: list[Event] = []
        t = at(8)
        for i in range(20):
            events.extend(ticket_flow(f"t_m{i}", t, 7000, prefix=f"m{i:02d}"))
            t += timedelta(minutes=15)
        # 16:03 cluster: six forced declines in six minutes.
        burst = at(16, 3)
        for i in range(6):
            events.extend(
                ticket_flow(
                    f"t_f{i}",
                    burst + timedelta(minutes=i),
                    7000,
                    prefix=f"f{i}",
                    fail=True,
                )
            )
        hits = detect_payment_failures(events, window_s=8 * 60, min_payments=5, z_thresh=2.5)
        self.assertGreaterEqual(len(hits), 1)
        top = max(hits, key=lambda a: a.score)
        self.assertGreaterEqual(top.score, 2.5)
        self.assertGreaterEqual(int(top.details["failures"]), 5)
        self.assertTrue(any(eid.startswith("f") for eid in top.event_ids))

    def test_collapses_overlapping_windows_to_peak(self) -> None:
        events: list[Event] = []
        t = at(8)
        for i in range(12):
            events.extend(ticket_flow(f"t_{i}", t, 7000, prefix=f"m{i:02d}"))
            t += timedelta(minutes=12)
        burst = at(16, 0)
        for i in range(6):
            events.extend(
                ticket_flow(f"t_f{i}", burst + timedelta(seconds=40 * i), 7000, prefix=f"f{i}", fail=True)
            )
        hits = detect_payment_failures(events, window_s=8 * 60, min_payments=5)
        self.assertEqual(len(hits), 1)


class VelocityDetectorTests(unittest.TestCase):
    def test_flags_fleet_dump(self) -> None:
        events: list[Event] = []
        # Quiet morning: one ticket every 12 minutes from 07:00.
        t = at(7, 0)
        n = 0
        while t < at(11, 25):
            events.extend(ticket_flow(f"t_{n}", t, 7000, prefix=f"q{n:02d}"))
            n += 1
            t += timedelta(minutes=12)
        fleet = at(11, 30)
        for i in range(8):
            events.extend(
                ticket_flow(f"t_fleet{i}", fleet + timedelta(seconds=15 * i), 7000, prefix=f"v{i}")
            )
        hits = detect_velocity(events, bin_minutes=5, lookback=8, z_thresh=2.6, min_bins=6)
        self.assertGreaterEqual(len(hits), 1)
        top = max(hits, key=lambda a: a.score)
        self.assertGreaterEqual(top.details["count"], 6)
        self.assertTrue(any(eid.startswith("v") for eid in top.event_ids))

    def test_two_ticket_blip_is_not_a_rush(self) -> None:
        events: list[Event] = []
        t = at(7, 0)
        for i in range(16):
            events.extend(ticket_flow(f"t_{i}", t, 7000, prefix=f"q{i:02d}"))
            t += timedelta(minutes=12)
        # Two cars in one 5-minute bin after a quiet stretch is just life.
        blip = at(11, 0)
        events.extend(ticket_flow("t_a", blip, 7000, prefix="a"))
        events.extend(ticket_flow("t_b", blip + timedelta(seconds=40), 7000, prefix="b"))
        hits = detect_velocity(events)
        self.assertEqual(hits, [])

    def test_empty_day_is_quiet(self) -> None:
        self.assertEqual(detect([]), [])
        self.assertEqual(detect_velocity([ev("e", EventType.TICKET_OPENED, at(8), "t")]), [])


class DwellDetectorTests(unittest.TestCase):
    def test_flags_bay_sitting_for_hours(self) -> None:
        events: list[Event] = []
        t = at(8)
        for i in range(12):
            events.extend(
                ticket_flow(f"t_{i:02d}", t, 7000 + i * 50, prefix=f"n{i:02d}")
            )
            t += timedelta(minutes=12)
        events.extend(
            ticket_flow(
                "t_stuck",
                t,
                7000,
                prefix="d",
                dwell=timedelta(hours=3, minutes=10),
            )
        )
        hits = detect_ticket_dwell(events, window=16, min_samples=8, z_thresh=2.8)
        self.assertTrue(any(a.ticket_id == "t_stuck" for a in hits))
        stuck = next(a for a in hits if a.ticket_id == "t_stuck")
        self.assertGreaterEqual(stuck.score, 2.8)
        self.assertGreaterEqual(stuck.details["dwell_minutes"], 180)
        self.assertEqual(len(stuck.event_ids), 2)

    def test_warmup_does_not_flag_first_long_ticket(self) -> None:
        events: list[Event] = []
        t = at(8)
        events.extend(
            ticket_flow(
                "t_long",
                t,
                7000,
                prefix="b",
                dwell=timedelta(hours=4),
            )
        )
        t += timedelta(minutes=10)
        for i in range(6):
            events.extend(ticket_flow(f"t_{i}", t, 7000, prefix=f"n{i}"))
            t += timedelta(minutes=10)
        hits = detect_ticket_dwell(events, min_samples=8)
        self.assertEqual(hits, [])

    def test_normal_oil_changes_are_quiet(self) -> None:
        events: list[Event] = []
        t = at(8)
        for i in range(16):
            events.extend(ticket_flow(f"t_{i:02d}", t, 7000, prefix=f"n{i:02d}"))
            t += timedelta(minutes=12)
        self.assertEqual(detect_ticket_dwell(events), [])
