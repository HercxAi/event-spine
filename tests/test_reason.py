from __future__ import annotations

import json
import unittest

from event_spine.events import EventType
from event_spine.reason import by_reason
from event_spine.report import render_reason, render_reason_json
from event_spine.simulate import SimConfig, simulate_day
from tests.helpers import at, ev, ticket_flow


class ReasonFoldTests(unittest.TestCase):
    def test_groups_by_reason(self) -> None:
        events = [
            *ticket_flow("t_a", at(8), 4000, prefix="a", fail=True),
            ev(
                "f1",
                EventType.PAYMENT_FAILED,
                at(9),
                "t_b",
                method="card",
                amount_cents=5000,
                reason="network",
            ),
            ev(
                "f2",
                EventType.PAYMENT_FAILED,
                at(9, 1),
                "t_c",
                method="card",
                amount_cents=3000,
                reason="network",
            ),
        ]
        rows = {row.reason: row for row in by_reason(events)}
        self.assertEqual(set(rows), {"declined", "network"})
        self.assertEqual(rows["declined"].fails, 1)
        self.assertEqual(rows["declined"].ask_cents, 4000)
        self.assertEqual(rows["declined"].methods, ("card",))
        self.assertEqual(rows["network"].fails, 2)
        self.assertEqual(rows["network"].ask_cents, 8000)
        self.assertEqual(rows["network"].methods, ("card",))

    def test_empty_log_is_empty_list(self) -> None:
        self.assertEqual(by_reason([]), [])

    def test_ignores_captures(self) -> None:
        events = ticket_flow("t_ok", at(8), 2000, prefix="ok")
        self.assertEqual(by_reason(events), [])

    def test_sorts_by_fails_then_reason(self) -> None:
        events = [
            ev(
                "a",
                EventType.PAYMENT_FAILED,
                at(8),
                "t1",
                method="card",
                amount_cents=1,
                reason="zebra",
            ),
            ev(
                "b",
                EventType.PAYMENT_FAILED,
                at(8, 1),
                "t2",
                method="card",
                amount_cents=1,
                reason="alpha",
            ),
            ev(
                "c",
                EventType.PAYMENT_FAILED,
                at(8, 2),
                "t3",
                method="cash",
                amount_cents=1,
                reason="alpha",
            ),
        ]
        rows = by_reason(events)
        self.assertEqual([row.reason for row in rows], ["alpha", "zebra"])
        self.assertEqual(rows[0].methods, ("card", "cash"))

    def test_seeded_day_is_network_on_card(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        rows = by_reason(events)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].reason, "network")
        self.assertEqual(rows[0].methods, ("card",))
        self.assertGreater(rows[0].fails, 0)
        self.assertGreater(rows[0].ask_cents, 0)


class ReasonRenderTests(unittest.TestCase):
    def test_human_and_json_match_fold(self) -> None:
        events = [
            ev(
                "f1",
                EventType.PAYMENT_FAILED,
                at(8),
                "t1",
                method="card",
                amount_cents=9999,
                reason="network",
            ),
        ]
        rows = by_reason(events)
        text = render_reason(events, rows)
        self.assertIn("reason", text)
        self.assertIn("network", text)
        self.assertIn("1 fails", text)
        payload = json.loads(render_reason_json(events, rows))
        self.assertEqual(payload["events"], 1)
        self.assertEqual(
            [(r["reason"], r["fails"], r["ask_cents"]) for r in payload["reasons"]],
            [(row.reason, row.fails, row.ask_cents) for row in rows],
        )
