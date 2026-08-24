from __future__ import annotations

import math
import unittest
from datetime import timedelta

from event_spine.detect import (
    MAD_CONSISTENCY,
    cusum_highside,
    detect,
    detect_concurrent_open,
    detect_payment_failure_cusum,
    detect_payment_failure_ewma,
    detect_payment_failures,
    detect_ticket_dwell,
    detect_ticket_totals,
    detect_ticket_totals_iqr,
    detect_ticket_totals_mad,
    detect_velocity,
    percentile,
    ewma_highside,
    ewma_ucl,
    mad,
    median,
    modified_zscore,
    proportion_z,
    sample_mean_std,
    tabular_cusum_k,
    tukey_highside,
    zscore,
)
from event_spine.events import Event, EventType
from event_spine.simulate import SimConfig, simulate_day
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

    def test_median_odd_and_even(self) -> None:
        self.assertEqual(median([3.0, 1.0, 2.0]), 2.0)
        # [9, 10, 10, 11, 11, 12, 12, 13] → (11+11)/2
        self.assertEqual(median([10.0, 12.0, 11.0, 13.0, 10.0, 12.0, 11.0, 9.0]), 11.0)
        self.assertIsNone(median([]))

    def test_mad_hand_computed(self) -> None:
        # median 11; absdevs [1, 1, 0, 2, 1, 1, 0, 2]
        # sorted [0, 0, 1, 1, 1, 1, 2, 2]; even → (1+1)/2 = 1
        values = [10.0, 12.0, 11.0, 13.0, 10.0, 12.0, 11.0, 9.0]
        self.assertEqual(mad(values), 1.0)

    def test_modified_zscore_hand_computed(self) -> None:
        baseline = [10.0, 12.0, 11.0, 13.0, 10.0, 12.0, 11.0, 9.0]
        z = modified_zscore(20.0, baseline)
        assert z is not None
        self.assertAlmostEqual(z, MAD_CONSISTENCY * (20 - 11) / 1.0)

    def test_modified_zscore_zero_mad(self) -> None:
        self.assertEqual(modified_zscore(5.0, [5.0, 5.0, 5.0]), 0.0)
        self.assertEqual(modified_zscore(9.0, [5.0, 5.0, 5.0]), math.inf)

    def test_modified_zscore_needs_two_samples(self) -> None:
        self.assertIsNone(modified_zscore(10.0, [10.0]))

    def test_percentile_type7_hand_computed(self) -> None:
        # n=8, ordered [9,10,10,11,11,12,12,13]
        # Q1: idx=1.75 → 10; Q3: idx=5.25 → 12
        values = [10.0, 12.0, 11.0, 13.0, 10.0, 12.0, 11.0, 9.0]
        self.assertAlmostEqual(percentile(values, 0.25), 10.0)
        self.assertAlmostEqual(percentile(values, 0.75), 12.0)
        self.assertEqual(percentile(values, 0.0), 9.0)
        self.assertEqual(percentile(values, 1.0), 13.0)
        self.assertIsNone(percentile([], 0.5))
        self.assertEqual(percentile([7.0], 0.3), 7.0)

    def test_tukey_highside_hand_computed(self) -> None:
        baseline = [10.0, 12.0, 11.0, 13.0, 10.0, 12.0, 11.0, 9.0]
        # (20 − 12) / 2 = 4
        z = tukey_highside(20.0, baseline)
        assert z is not None
        self.assertAlmostEqual(z, 4.0)

    def test_tukey_highside_zero_iqr(self) -> None:
        self.assertEqual(tukey_highside(5.0, [5.0, 5.0, 5.0, 5.0]), 0.0)
        self.assertEqual(tukey_highside(9.0, [5.0, 5.0, 5.0, 5.0]), math.inf)

    def test_tukey_highside_needs_four_samples(self) -> None:
        self.assertIsNone(tukey_highside(10.0, [9.0, 10.0, 11.0]))

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

    def test_tabular_cusum_k_hand_computed(self) -> None:
        # p0=0.03, σ=√(0.03·0.97), k = p0 + ½σ
        k = tabular_cusum_k(0.03)
        assert k is not None
        self.assertAlmostEqual(k, 0.03 + 0.5 * math.sqrt(0.03 * 0.97))
        self.assertIsNone(tabular_cusum_k(0.0))
        self.assertIsNone(tabular_cusum_k(1.0))

    def test_cusum_highside_quiet_then_fail_burst(self) -> None:
        # Ten captures, then six fails. k from morning p0=0.03.
        k = tabular_cusum_k(0.03)
        assert k is not None
        xs = [0.0] * 10 + [1.0] * 6
        hits = cusum_highside(xs, k=k, h=4.0)
        self.assertEqual(len(hits), 1)
        decision_i, change_i, statistic = hits[0]
        # S grows by (1−k) per fail; first crossing is the 5th fail.
        self.assertEqual(change_i, 10)
        self.assertEqual(decision_i, 14)
        self.assertAlmostEqual(statistic, 5 * (1.0 - k))
        self.assertGreaterEqual(statistic, 4.0)

    def test_cusum_highside_latches_until_reset(self) -> None:
        k = tabular_cusum_k(0.03)
        assert k is not None
        # One excursion of six fails, then captures that drain S, then another burst.
        drain = math.ceil(6 * (1.0 - k) / k) + 1
        xs = [0.0] * 8 + [1.0] * 6 + [0.0] * drain + [1.0] * 6
        hits = cusum_highside(xs, k=k, h=4.0)
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0][1], 8)
        self.assertGreater(hits[1][1], hits[0][0])

    def test_ewma_ucl_hand_computed(self) -> None:
        # p0=0.03, σ=√(0.03·0.97), λ=0.1, L=3
        # UCL = p0 + 3σ√(λ/(2−λ))
        ucl = ewma_ucl(0.03, math.sqrt(0.03 * 0.97), lam=0.1, L=3.0)
        assert ucl is not None
        self.assertAlmostEqual(
            ucl, 0.03 + 3.0 * math.sqrt(0.03 * 0.97) * math.sqrt(0.1 / 1.9)
        )
        self.assertIsNone(ewma_ucl(0.03, 0.0, lam=0.1, L=3.0))
        self.assertIsNone(ewma_ucl(0.03, 0.2, lam=0.0, L=3.0))
        self.assertIsNone(ewma_ucl(0.03, 0.2, lam=0.1, L=0.0))

    def test_ewma_highside_quiet_then_fail_burst(self) -> None:
        # Ten captures, then fails. Z_0 = p0; each capture pulls Z toward 0.
        lam, L, p0 = 0.1, 3.0, 0.03
        sigma = math.sqrt(p0 * (1.0 - p0))
        ucl = ewma_ucl(p0, sigma, lam=lam, L=L)
        assert ucl is not None
        xs = [0.0] * 10 + [1.0] * 3
        hits = ewma_highside(xs, mu0=p0, lam=lam, L=L, sigma=sigma)
        self.assertEqual(len(hits), 1)
        decision_i, change_i, statistic = hits[0]
        z = p0 * (1.0 - lam) ** 10
        z1 = lam * 1.0 + (1.0 - lam) * z
        z2 = lam * 1.0 + (1.0 - lam) * z1
        self.assertLess(z1, ucl)
        self.assertGreaterEqual(z2, ucl)
        self.assertEqual(change_i, 10)
        self.assertEqual(decision_i, 11)
        self.assertAlmostEqual(statistic, z2)

    def test_ewma_highside_latches_until_reset(self) -> None:
        lam, L, p0 = 0.1, 3.0, 0.03
        sigma = math.sqrt(p0 * (1.0 - p0))
        # Two fails trip; then enough captures to pull Z back to p0; then another burst.
        xs = [0.0] * 8 + [1.0] * 3 + [0.0] * 40 + [1.0] * 3
        hits = ewma_highside(xs, mu0=p0, lam=lam, L=L, sigma=sigma)
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0][1], 8)
        self.assertGreater(hits[1][1], hits[0][0])


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


class TicketTotalMadDetectorTests(unittest.TestCase):
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
        hits = detect_ticket_totals_mad(events, window=16, min_samples=8, z_thresh=3.5)
        self.assertTrue(any(a.ticket_id == "t_whale" for a in hits))
        whale = next(a for a in hits if a.ticket_id == "t_whale")
        self.assertGreaterEqual(whale.score, 3.5)
        self.assertEqual(whale.event_ids[0].startswith("w"), True)
        self.assertEqual(whale.detector, "ticket_total_mad")

    def test_warmup_does_not_flag_first_tickets(self) -> None:
        events: list[Event] = []
        t = at(8)
        events.extend(ticket_flow("t_big", t, 80_000, prefix="b"))
        t += timedelta(minutes=10)
        for i in range(6):
            events.extend(ticket_flow(f"t_{i}", t, 7000, prefix=f"n{i}"))
            t += timedelta(minutes=10)
        hits = detect_ticket_totals_mad(events, min_samples=8)
        self.assertEqual(hits, [])

    def test_prior_whale_masks_bessel_not_mad(self) -> None:
        """A first whale inflates sample σ; Bessel misses the second, MAD does not."""
        events: list[Event] = []
        t = at(8)
        for i in range(8):
            events.extend(
                ticket_flow(f"t_{i:02d}", t, 7000 + i * 50, prefix=f"n{i:02d}")
            )
            t += timedelta(minutes=12)
        events.extend(
            ticket_flow("t_whale1", t, 55_000, prefix="w1", items=[("TRN-FLUSH", 55_000)])
        )
        t += timedelta(minutes=12)
        for i in range(6):
            events.extend(
                ticket_flow(f"t_m{i}", t, 7100 + i * 40, prefix=f"m{i:02d}")
            )
            t += timedelta(minutes=12)
        events.extend(
            ticket_flow("t_whale2", t, 55_000, prefix="w2", items=[("TRN-FLUSH", 55_000)])
        )
        bessel = detect_ticket_totals(events, window=8, min_samples=8, z_thresh=2.8)
        robust = detect_ticket_totals_mad(events, window=8, min_samples=8, z_thresh=3.5)
        bessel_ids = {a.ticket_id for a in bessel}
        robust_ids = {a.ticket_id for a in robust}
        self.assertIn("t_whale1", bessel_ids)
        self.assertIn("t_whale1", robust_ids)
        self.assertNotIn("t_whale2", bessel_ids)
        self.assertIn("t_whale2", robust_ids)
        second = next(a for a in robust if a.ticket_id == "t_whale2")
        self.assertGreaterEqual(second.score, 3.5)

    def test_seeded_day_flags_flush_ticket(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        hits = detect_ticket_totals_mad(events)
        self.assertGreaterEqual(len(hits), 1)
        top = max(hits, key=lambda a: a.score)
        self.assertGreaterEqual(top.score, 3.5)
        self.assertGreaterEqual(int(top.details["total_cents"]), 40_000)
        names = {a.detector for a in detect(events)}
        self.assertIn("ticket_total", names)
        self.assertIn("ticket_total_mad", names)


class TicketTotalIqrDetectorTests(unittest.TestCase):
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
        hits = detect_ticket_totals_iqr(events, window=16, min_samples=8, k=1.5)
        self.assertTrue(any(a.ticket_id == "t_whale" for a in hits))
        whale = next(a for a in hits if a.ticket_id == "t_whale")
        self.assertGreaterEqual(whale.score, 1.5)
        self.assertEqual(whale.event_ids[0].startswith("w"), True)
        self.assertEqual(whale.detector, "ticket_total_iqr")

    def test_warmup_does_not_flag_first_tickets(self) -> None:
        events: list[Event] = []
        t = at(8)
        events.extend(ticket_flow("t_big", t, 80_000, prefix="b"))
        t += timedelta(minutes=10)
        for i in range(6):
            events.extend(ticket_flow(f"t_{i}", t, 7000, prefix=f"n{i}"))
            t += timedelta(minutes=10)
        hits = detect_ticket_totals_iqr(events, min_samples=8)
        self.assertEqual(hits, [])

    def test_prior_whale_masks_bessel_not_iqr(self) -> None:
        """A first whale inflates sample σ; Bessel misses the second, Tukey does not."""
        events: list[Event] = []
        t = at(8)
        for i in range(8):
            events.extend(
                ticket_flow(f"t_{i:02d}", t, 7000 + i * 50, prefix=f"n{i:02d}")
            )
            t += timedelta(minutes=12)
        events.extend(
            ticket_flow("t_whale1", t, 55_000, prefix="w1", items=[("TRN-FLUSH", 55_000)])
        )
        t += timedelta(minutes=12)
        for i in range(6):
            events.extend(
                ticket_flow(f"t_m{i}", t, 7100 + i * 40, prefix=f"m{i:02d}")
            )
            t += timedelta(minutes=12)
        events.extend(
            ticket_flow("t_whale2", t, 55_000, prefix="w2", items=[("TRN-FLUSH", 55_000)])
        )
        bessel = detect_ticket_totals(events, window=8, min_samples=8, z_thresh=2.8)
        fences = detect_ticket_totals_iqr(events, window=8, min_samples=8, k=1.5)
        bessel_ids = {a.ticket_id for a in bessel}
        fence_ids = {a.ticket_id for a in fences}
        self.assertIn("t_whale1", bessel_ids)
        self.assertIn("t_whale1", fence_ids)
        self.assertNotIn("t_whale2", bessel_ids)
        self.assertIn("t_whale2", fence_ids)
        second = next(a for a in fences if a.ticket_id == "t_whale2")
        self.assertGreaterEqual(second.score, 1.5)

    def test_seeded_day_flags_flush_ticket(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        hits = detect_ticket_totals_iqr(events)
        self.assertGreaterEqual(len(hits), 1)
        top = max(hits, key=lambda a: a.score)
        self.assertGreaterEqual(top.score, 1.5)
        self.assertGreaterEqual(int(top.details["total_cents"]), 40_000)
        names = {a.detector for a in detect(events)}
        self.assertIn("ticket_total_iqr", names)


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


class PaymentCusumDetectorTests(unittest.TestCase):
    def test_flags_afternoon_fail_burst(self) -> None:
        events: list[Event] = []
        t = at(8)
        for i in range(20):
            events.append(
                ev(f"m{i:02d}", EventType.PAYMENT_CAPTURED, t, f"t_m{i}")
            )
            t += timedelta(minutes=10)
        burst = at(16, 3)
        for i in range(6):
            events.append(
                ev(
                    f"f{i}",
                    EventType.PAYMENT_FAILED,
                    burst + timedelta(minutes=i),
                    f"t_f{i}",
                )
            )
        hits = detect_payment_failure_cusum(events)
        self.assertEqual(len(hits), 1)
        hit = hits[0]
        self.assertEqual(hit.detector, "payment_failure_cusum")
        self.assertGreaterEqual(hit.score, 4.0)
        self.assertEqual(hit.details["change_event_id"], "f0")
        self.assertTrue(hit.details["decision_event_id"].startswith("f"))
        self.assertIn("f0", hit.event_ids)
        self.assertEqual(hit.details["baseline_until_hour"], 14)
        self.assertLess(hit.details["baseline_failures"], 1)
        k = tabular_cusum_k(0.03)
        assert k is not None
        self.assertAlmostEqual(float(hit.details["k"]), k)
        self.assertAlmostEqual(hit.score, 5 * (1.0 - k))

    def test_one_change_point_not_a_hit_per_fail(self) -> None:
        events: list[Event] = []
        t = at(8)
        for i in range(16):
            events.append(
                ev(f"m{i:02d}", EventType.PAYMENT_CAPTURED, t, f"t_m{i}")
            )
            t += timedelta(minutes=12)
        burst = at(16, 3)
        for i in range(10):
            events.append(
                ev(
                    f"f{i}",
                    EventType.PAYMENT_FAILED,
                    burst + timedelta(seconds=30 * i),
                    f"t_f{i}",
                )
            )
        hits = detect_payment_failure_cusum(events)
        self.assertEqual(len(hits), 1)

    def test_isolated_fail_is_quiet(self) -> None:
        events: list[Event] = []
        t = at(8)
        for i in range(16):
            events.append(
                ev(f"m{i:02d}", EventType.PAYMENT_CAPTURED, t, f"t_m{i}")
            )
            t += timedelta(minutes=12)
        events.append(ev("f0", EventType.PAYMENT_FAILED, at(16, 3), "t_f"))
        self.assertEqual(detect_payment_failure_cusum(events), [])

    def test_baseline_excludes_afternoon_outage(self) -> None:
        events: list[Event] = []
        t = at(8)
        for i in range(12):
            events.append(
                ev(f"m{i:02d}", EventType.PAYMENT_CAPTURED, t, f"t_m{i}")
            )
            t += timedelta(minutes=15)
        # A morning-looking burst after 14:00 must not raise p0.
        burst = at(16, 3)
        for i in range(6):
            events.append(
                ev(
                    f"f{i}",
                    EventType.PAYMENT_FAILED,
                    burst + timedelta(minutes=i),
                    f"t_f{i}",
                )
            )
        hit = detect_payment_failure_cusum(events)[0]
        self.assertEqual(hit.details["baseline_failures"], 0)
        self.assertAlmostEqual(float(hit.details["p0"]), 0.03)

    def test_seeded_day_flags_1603_outage(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        hits = detect_payment_failure_cusum(events)
        self.assertEqual(len(hits), 1)
        hit = hits[0]
        self.assertEqual(hit.detector, "payment_failure_cusum")
        self.assertGreaterEqual(hit.score, 4.0)
        self.assertEqual(hit.at.hour, 16)
        self.assertGreaterEqual(hit.at.minute, 3)
        self.assertLess(hit.at.minute, 15)
        change_at = hit.details["change_at"]
        self.assertTrue(str(change_at).startswith("2026-03-14T16:"))
        self.assertTrue(hit.event_ids)
        names = {a.detector for a in detect(events)}
        self.assertIn("payment_failure", names)
        self.assertIn("payment_failure_cusum", names)
        self.assertIn("payment_failure_ewma", names)


class PaymentEwmaDetectorTests(unittest.TestCase):
    def test_flags_afternoon_fail_burst(self) -> None:
        events: list[Event] = []
        t = at(8)
        for i in range(20):
            events.append(
                ev(f"m{i:02d}", EventType.PAYMENT_CAPTURED, t, f"t_m{i}")
            )
            t += timedelta(minutes=10)
        burst = at(16, 3)
        for i in range(6):
            events.append(
                ev(
                    f"f{i}",
                    EventType.PAYMENT_FAILED,
                    burst + timedelta(minutes=i),
                    f"t_f{i}",
                )
            )
        hits = detect_payment_failure_ewma(events)
        self.assertEqual(len(hits), 1)
        hit = hits[0]
        self.assertEqual(hit.detector, "payment_failure_ewma")
        self.assertGreaterEqual(hit.score, 3.0)
        self.assertEqual(hit.details["change_event_id"], "f0")
        self.assertTrue(hit.details["decision_event_id"].startswith("f"))
        self.assertIn("f0", hit.event_ids)
        self.assertEqual(hit.details["baseline_until_hour"], 14)
        self.assertLess(hit.details["baseline_failures"], 1)
        lam, L, p0 = 0.1, 3.0, 0.03
        sigma = math.sqrt(p0 * (1.0 - p0))
        ucl = ewma_ucl(p0, sigma, lam=lam, L=L)
        assert ucl is not None
        self.assertAlmostEqual(float(hit.details["UCL"]), ucl)
        self.assertAlmostEqual(float(hit.details["lambda"]), lam)
        self.assertGreaterEqual(float(hit.details["Z"]), ucl)

    def test_one_change_point_not_a_hit_per_fail(self) -> None:
        events: list[Event] = []
        t = at(8)
        for i in range(16):
            events.append(
                ev(f"m{i:02d}", EventType.PAYMENT_CAPTURED, t, f"t_m{i}")
            )
            t += timedelta(minutes=12)
        burst = at(16, 3)
        for i in range(10):
            events.append(
                ev(
                    f"f{i}",
                    EventType.PAYMENT_FAILED,
                    burst + timedelta(seconds=30 * i),
                    f"t_f{i}",
                )
            )
        hits = detect_payment_failure_ewma(events)
        self.assertEqual(len(hits), 1)

    def test_isolated_fail_is_quiet(self) -> None:
        events: list[Event] = []
        t = at(8)
        for i in range(16):
            events.append(
                ev(f"m{i:02d}", EventType.PAYMENT_CAPTURED, t, f"t_m{i}")
            )
            t += timedelta(minutes=12)
        events.append(ev("f0", EventType.PAYMENT_FAILED, at(16, 3), "t_f"))
        self.assertEqual(detect_payment_failure_ewma(events), [])

    def test_slow_burn_flags_before_cusum(self) -> None:
        # Morning captures, then a 1-in-7 fail drip (≈14%). CUSUM stays
        # below h=4; the EWMA crosses UCL on the same stream.
        events: list[Event] = []
        t = at(8)
        for i in range(20):
            events.append(
                ev(f"m{i:02d}", EventType.PAYMENT_CAPTURED, t, f"t_m{i}")
            )
            t += timedelta(minutes=10)
        drip = at(15, 0)
        for i in range(15):
            events.append(
                ev(
                    f"f{i}",
                    EventType.PAYMENT_FAILED,
                    drip + timedelta(minutes=7 * i),
                    f"t_f{i}",
                )
            )
            for j in range(6):
                events.append(
                    ev(
                        f"c{i:02d}{j}",
                        EventType.PAYMENT_CAPTURED,
                        drip + timedelta(minutes=7 * i, seconds=20 * (j + 1)),
                        f"t_c{i}_{j}",
                    )
                )
        events.sort(key=lambda e: (e.occurred_at, e.event_id))
        self.assertEqual(detect_payment_failure_cusum(events), [])
        hits = detect_payment_failure_ewma(events)
        self.assertEqual(len(hits), 1)
        hit = hits[0]
        self.assertEqual(hit.detector, "payment_failure_ewma")
        self.assertGreaterEqual(hit.score, 3.0)
        self.assertEqual(hit.details["change_event_id"], "f0")
        self.assertGreaterEqual(hit.at.hour, 15)

    def test_baseline_excludes_afternoon_outage(self) -> None:
        events: list[Event] = []
        t = at(8)
        for i in range(12):
            events.append(
                ev(f"m{i:02d}", EventType.PAYMENT_CAPTURED, t, f"t_m{i}")
            )
            t += timedelta(minutes=15)
        burst = at(16, 3)
        for i in range(6):
            events.append(
                ev(
                    f"f{i}",
                    EventType.PAYMENT_FAILED,
                    burst + timedelta(minutes=i),
                    f"t_f{i}",
                )
            )
        hit = detect_payment_failure_ewma(events)[0]
        self.assertEqual(hit.details["baseline_failures"], 0)
        self.assertAlmostEqual(float(hit.details["p0"]), 0.03)

    def test_seeded_day_flags_1603_outage(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        hits = detect_payment_failure_ewma(events)
        self.assertEqual(len(hits), 1)
        hit = hits[0]
        self.assertEqual(hit.detector, "payment_failure_ewma")
        self.assertGreaterEqual(hit.score, 3.0)
        self.assertEqual(hit.at.hour, 16)
        self.assertGreaterEqual(hit.at.minute, 3)
        self.assertLess(hit.at.minute, 15)
        change_at = hit.details["change_at"]
        self.assertTrue(str(change_at).startswith("2026-03-14T16:"))
        self.assertTrue(hit.event_ids)
        cusum = detect_payment_failure_cusum(events)
        self.assertEqual(len(cusum), 1)
        # Smoother should speak on or before the CUSUM decision.
        self.assertLessEqual(hit.at, cusum[0].at)
        names = {a.detector for a in detect(events)}
        self.assertIn("payment_failure_ewma", names)


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

    def test_flags_ticket_still_open_for_hours(self) -> None:
        events: list[Event] = []
        t = at(8)
        for i in range(12):
            events.extend(
                ticket_flow(
                    f"t_{i:02d}",
                    t,
                    7000,
                    prefix=f"n{i:02d}",
                    dwell=timedelta(minutes=7 + (i % 3)),
                )
            )
            t += timedelta(minutes=12)
        opened = at(10, 30)
        events.append(
            ev("o01", EventType.TICKET_OPENED, opened, "t_open", bay="2", vehicle="x")
        )
        # A later close gives the log a "now" well after the stall started.
        events.extend(
            ticket_flow(
                "t_later",
                at(13, 40),
                7000,
                prefix="z",
                dwell=timedelta(minutes=8),
            )
        )
        hits = detect_ticket_dwell(events, window=16, min_samples=8, z_thresh=2.8)
        self.assertTrue(any(a.ticket_id == "t_open" for a in hits))
        stuck = next(a for a in hits if a.ticket_id == "t_open")
        self.assertGreaterEqual(stuck.score, 2.8)
        self.assertGreaterEqual(stuck.details["dwell_minutes"], 180)
        self.assertTrue(stuck.details["open"])
        self.assertEqual(stuck.event_ids, ("o01",))
        self.assertIn("still open", stuck.summary)

    def test_recently_opened_ticket_is_quiet(self) -> None:
        events: list[Event] = []
        t = at(8)
        for i in range(12):
            events.extend(
                ticket_flow(
                    f"t_{i:02d}",
                    t,
                    7000,
                    prefix=f"n{i:02d}",
                    dwell=timedelta(minutes=7 + (i % 3)),
                )
            )
            t += timedelta(minutes=12)
        # Opened a minute after the last arrival — still a normal oil change.
        events.append(
            ev(
                "o01",
                EventType.TICKET_OPENED,
                t + timedelta(minutes=1),
                "t_open",
                bay="2",
                vehicle="x",
            )
        )
        events.extend(
            ticket_flow(
                "t_last",
                t,
                7000,
                prefix="z",
                dwell=timedelta(minutes=8),
            )
        )
        hits = detect_ticket_dwell(events)
        self.assertFalse(any(a.ticket_id == "t_open" for a in hits))

    def test_open_ticket_respects_warmup(self) -> None:
        events = [
            ev("o01", EventType.TICKET_OPENED, at(8), "t_open", bay="1", vehicle="x"),
            *ticket_flow(
                "t_done",
                at(11),
                7000,
                prefix="c",
                dwell=timedelta(hours=3),
            ),
        ]
        # Only one closed ticket; not enough baseline to score the open stall.
        self.assertEqual(detect_ticket_dwell(events, min_samples=8), [])


class ConcurrentOpenDetectorTests(unittest.TestCase):
    def test_score_matches_hand_computed_series(self) -> None:
        # Eight sequential open/close pairs → concurrent snapshots [1, 0] * 8.
        # Then six stacked opens. The 6-open snapshot is judged against the
        # prior 16 counts: the last 11 quiet values plus the climb 1..5.
        events: list[Event] = []
        t = at(8)
        for i in range(8):
            events.append(ev(f"q{i}o", EventType.TICKET_OPENED, t, f"t_q{i}"))
            t += timedelta(minutes=4)
            events.append(ev(f"q{i}c", EventType.TICKET_CLOSED, t, f"t_q{i}"))
            t += timedelta(minutes=4)
        pile = t
        for i in range(6):
            events.append(
                ev(
                    f"p{i}o",
                    EventType.TICKET_OPENED,
                    pile + timedelta(seconds=10 * i),
                    f"t_p{i}",
                )
            )
        quiet = [1.0, 0.0] * 8
        baseline = (quiet + [1.0, 2.0, 3.0, 4.0, 5.0])[-16:]
        expected = zscore(6.0, baseline)
        assert expected is not None

        hits = detect_concurrent_open(events, window=16, min_samples=8, z_thresh=2.8)
        self.assertTrue(hits)
        top = max(hits, key=lambda a: a.score)
        self.assertEqual(top.detector, "concurrent_open")
        self.assertEqual(top.details["concurrent"], 6)
        self.assertAlmostEqual(top.score, expected)
        self.assertEqual(len(top.event_ids), 6)
        self.assertTrue(all(eid.startswith("p") for eid in top.event_ids))

    def test_flags_pileup_against_quiet_baseline(self) -> None:
        events: list[Event] = []
        t = at(8)
        for i in range(12):
            events.extend(ticket_flow(f"t_{i:02d}", t, 7000, prefix=f"n{i:02d}"))
            t += timedelta(minutes=12)
        pile = t
        for i in range(8):
            events.extend(
                ticket_flow(
                    f"t_p{i}",
                    pile + timedelta(seconds=8 * i),
                    7000,
                    prefix=f"p{i}",
                )
            )
        hits = detect_concurrent_open(events, window=16, min_samples=8, z_thresh=2.8)
        self.assertGreaterEqual(len(hits), 1)
        top = max(hits, key=lambda a: a.score)
        self.assertGreaterEqual(top.score, 2.8)
        self.assertGreaterEqual(int(top.details["concurrent"]), 5)
        self.assertTrue(any(eid.startswith("p") for eid in top.event_ids))

    def test_warmup_does_not_flag_first_stack(self) -> None:
        events: list[Event] = [
            ev(f"p{i}o", EventType.TICKET_OPENED, at(8, 0, i), f"t_p{i}")
            for i in range(8)
        ]
        self.assertEqual(detect_concurrent_open(events, min_samples=8), [])

    def test_sequential_oil_changes_are_quiet(self) -> None:
        events: list[Event] = []
        t = at(8)
        for i in range(16):
            events.extend(ticket_flow(f"t_{i:02d}", t, 7000, prefix=f"n{i:02d}"))
            t += timedelta(minutes=12)
        self.assertEqual(detect_concurrent_open(events), [])

    def test_two_cars_is_not_a_pileup(self) -> None:
        events: list[Event] = []
        t = at(8)
        for i in range(12):
            events.extend(ticket_flow(f"t_{i:02d}", t, 7000, prefix=f"n{i:02d}"))
            t += timedelta(minutes=12)
        overlap = t
        events.extend(ticket_flow("t_a", overlap, 7000, prefix="a"))
        events.extend(
            ticket_flow("t_b", overlap + timedelta(seconds=20), 7000, prefix="b")
        )
        self.assertEqual(detect_concurrent_open(events), [])

    def test_detect_includes_concurrent_open(self) -> None:
        events: list[Event] = []
        t = at(8)
        for i in range(10):
            events.extend(ticket_flow(f"t_{i:02d}", t, 7000, prefix=f"n{i:02d}"))
            t += timedelta(minutes=12)
        pile = t
        for i in range(8):
            events.append(
                ev(f"p{i}o", EventType.TICKET_OPENED, pile + timedelta(seconds=i), f"t_p{i}")
            )
        names = {a.detector for a in detect(events)}
        self.assertIn("concurrent_open", names)
