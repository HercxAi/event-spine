from __future__ import annotations

import unittest
from datetime import timedelta

from event_spine.detect import SILENT_GAP_MINUTES, detect, detect_silent_gap
from event_spine.events import Event, EventType
from event_spine.gaps import shop_open_gaps
from event_spine.hours import SHOP_CLOSE_HOUR, SHOP_OPEN_HOUR
from event_spine.report import render_gaps
from event_spine.simulate import SimConfig, simulate_day
from tests.helpers import at, ev, ticket_flow


def _opens_between(events: list[Event], start, end) -> int:
    return sum(
        1
        for e in events
        if e.type is EventType.TICKET_OPENED and start <= e.occurred_at < end
    )


class ShopOpenGapFoldTests(unittest.TestCase):
    def test_empty_log_has_no_gaps(self) -> None:
        self.assertEqual(shop_open_gaps([]), [])

    def test_measures_open_to_first_and_last_to_close(self) -> None:
        events = ticket_flow("t_1", at(8, 30), 4000, prefix="a")
        gaps = shop_open_gaps(events)
        self.assertEqual(gaps[0].start.hour, SHOP_OPEN_HOUR)
        self.assertEqual(gaps[0].start.minute, 0)
        self.assertEqual(gaps[0].before_event_id, None)
        self.assertEqual(gaps[0].after_event_id, "a01")
        self.assertAlmostEqual(gaps[0].minutes, 90.0)
        last = gaps[-1]
        self.assertEqual(last.end.hour, SHOP_CLOSE_HOUR)
        self.assertEqual(last.before_event_id, "a01")
        self.assertEqual(last.after_event_id, None)

    def test_after_hours_opens_do_not_bound_shop_hours(self) -> None:
        events = [
            *ticket_flow("t_day", at(10), 4000, prefix="d"),
            *ticket_flow("t_late", at(20, 30), 4000, prefix="n"),
        ]
        in_hours = [g for g in shop_open_gaps(events) if g.after_event_id == "n01"]
        self.assertEqual(in_hours, [])
        last = shop_open_gaps(events)[-1]
        self.assertEqual(last.end.hour, SHOP_CLOSE_HOUR)
        self.assertNotEqual(last.before_event_id, "n01")

    def test_fold_does_not_mutate_events(self) -> None:
        events = ticket_flow("t_1", at(8), 4000, prefix="m")
        before = [e.to_dict() for e in events]
        shop_open_gaps(events)
        self.assertEqual([e.to_dict() for e in events], before)


def _shop_day(*, step_min: int = 8, pause_at=None, pause_min: int = 0) -> list[Event]:
    """Oil-change cadence across shop hours, optional one dead stretch."""
    events: list[Event] = []
    t = at(7, 5)
    n = 0
    paused = False
    while t < at(18, 50):
        events.extend(ticket_flow(f"t_{n:03d}", t, 7000, prefix=f"n{n:03d}"))
        n += 1
        t += timedelta(minutes=step_min)
        if pause_at is not None and not paused and t >= pause_at:
            t += timedelta(minutes=pause_min)
            paused = True
    return events


class SilentGapDetectorTests(unittest.TestCase):
    def test_flags_dead_register_stretch(self) -> None:
        events = _shop_day(pause_at=at(10, 0), pause_min=50)
        hits = detect_silent_gap(events)
        self.assertEqual(len(hits), 1)
        hit = hits[0]
        self.assertEqual(hit.detector, "silent_gap")
        self.assertGreaterEqual(hit.score, SILENT_GAP_MINUTES)
        self.assertGreaterEqual(hit.details["gap_minutes"], 50)
        self.assertEqual(len(hit.event_ids), 2)
        self.assertGreaterEqual(hit.at.hour, 9)
        self.assertLess(hit.at.hour, 12)

    def test_busy_stretch_is_quiet(self) -> None:
        self.assertEqual(detect_silent_gap(_shop_day()), [])

    def test_just_under_threshold_is_quiet(self) -> None:
        events = [
            *ticket_flow("t_a", at(10), 7000, prefix="a"),
            *ticket_flow(
                "t_b",
                at(10) + timedelta(minutes=SILENT_GAP_MINUTES - 1),
                7000,
                prefix="b",
            ),
        ]
        # Shop-open and shop-close legs are hours long; only the
        # middle stretch should be judged here.
        mid = [
            a
            for a in detect_silent_gap(events)
            if a.details.get("before_event_id") == "a01"
            and a.details.get("after_event_id") == "b01"
        ]
        self.assertEqual(mid, [])

    def test_after_hours_silence_is_ignored(self) -> None:
        events = _shop_day()
        events.extend(ticket_flow("t_late1", at(19, 10), 7000, prefix="x"))
        events.extend(ticket_flow("t_late2", at(21, 0), 7000, prefix="y"))
        self.assertEqual(detect_silent_gap(events), [])

    def test_empty_day_is_quiet(self) -> None:
        self.assertEqual(detect_silent_gap([]), [])
        self.assertEqual(detect([]), [])

    def test_seeded_day_is_busy_enough(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        self.assertEqual(detect_silent_gap(events), [])
        names = {a.detector for a in detect(events)}
        self.assertNotIn("silent_gap", names)

    def test_planted_lunch_rush_gap_flags(self) -> None:
        events = simulate_day(
            SimConfig(
                seed=42,
                silent_gap_hour=12,
                silent_gap_minute=10,
                silent_gap_minutes=50,
            )
        )
        hole_start = at(12, 10)
        hole_end = at(13, 0)
        self.assertEqual(_opens_between(events, hole_start, hole_end), 0)
        hits = detect_silent_gap(events)
        self.assertGreaterEqual(len(hits), 1)
        lunch = max(hits, key=lambda a: a.score)
        self.assertEqual(lunch.detector, "silent_gap")
        self.assertGreaterEqual(lunch.score, SILENT_GAP_MINUTES)
        start = lunch.details["window_start"]
        end = lunch.details["window_end"]
        self.assertTrue(start.startswith("2026-03-14T12:"))
        self.assertTrue(end.startswith("2026-03-14T13:"))
        self.assertTrue(lunch.event_ids)
        names = {a.detector for a in detect(events)}
        self.assertIn("silent_gap", names)

    def test_planted_dead_register_stretch_flags(self) -> None:
        events = simulate_day(
            SimConfig(
                seed=42,
                silent_gap_hour=15,
                silent_gap_minute=10,
                silent_gap_minutes=50,
            )
        )
        self.assertEqual(_opens_between(events, at(15, 10), at(16, 0)), 0)
        hits = detect_silent_gap(events)
        self.assertGreaterEqual(len(hits), 1)
        dead = max(hits, key=lambda a: a.score)
        self.assertGreaterEqual(dead.score, SILENT_GAP_MINUTES)
        window = dead.details["window_start"]
        self.assertTrue(
            window.startswith("2026-03-14T14:") or window.startswith("2026-03-14T15:")
        )

    def test_render_mentions_threshold_and_ticket_opened(self) -> None:
        events = simulate_day(
            SimConfig(
                seed=42,
                silent_gap_hour=12,
                silent_gap_minute=10,
                silent_gap_minutes=50,
            )
        )
        text = render_gaps(events)
        self.assertIn("silent gaps", text)
        self.assertIn("TicketOpened", text)
        self.assertIn(f"{SILENT_GAP_MINUTES:g}min", text)
        self.assertIn("events:", text)

    def test_render_quiet_day(self) -> None:
        text = render_gaps(simulate_day(SimConfig(seed=42)))
        self.assertIn("no silent gaps", text)
        self.assertIn("silent gaps", text)
