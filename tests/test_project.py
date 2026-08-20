from __future__ import annotations

import unittest
from datetime import timedelta

from event_spine.events import EventType
from event_spine.project import apply, project
from tests.helpers import at, ev, ticket_flow


class ProjectTests(unittest.TestCase):
    def test_line_items_sum_to_ticket_total(self) -> None:
        events = ticket_flow(
            "t_1",
            at(8, 0),
            0,
            prefix="a",
            items=[("OIL-SYN", 6999), ("FIL-OIL", 1299), ("INSP", 0)],
        )
        ticket = project(events)["t_1"]
        self.assertEqual(ticket.total_cents, 8298)
        self.assertTrue(ticket.closed)
        self.assertTrue(ticket.paid)
        self.assertEqual(len(ticket.items), 3)

    def test_failed_then_captured_payment(self) -> None:
        events = ticket_flow("t_2", at(9), 5000, prefix="b", fail=True)
        ticket = project(events)["t_2"]
        self.assertEqual([p.ok for p in ticket.payments], [False, True])
        self.assertEqual(ticket.payments[0].reason, "declined")
        self.assertTrue(ticket.paid)

    def test_apply_is_pure(self) -> None:
        opened = ev("e1", EventType.TICKET_OPENED, at(8), "t", bay="2", vehicle="x")
        ticket = apply(None, opened)
        item = ev(
            "e2",
            EventType.LINE_ITEM_ADDED,
            at(8, 1),
            "t",
            sku="OIL-CONV",
            description="oil",
            qty=1,
            unit_cents=3999,
        )
        later = apply(ticket, item)
        self.assertEqual(ticket.items, ())
        self.assertEqual(later.items[0].ext_cents, 3999)
        self.assertIsNot(later, ticket)

    def test_orphan_line_item_raises(self) -> None:
        item = ev(
            "e2",
            EventType.LINE_ITEM_ADDED,
            at(8, 1),
            "t",
            sku="OIL-CONV",
            description="oil",
            qty=1,
            unit_cents=3999,
        )
        with self.assertRaises(ValueError):
            apply(None, item)

    def test_two_tickets_do_not_bleed(self) -> None:
        events = [
            *ticket_flow("t_a", at(8), 4000, prefix="a"),
            *ticket_flow("t_b", at(8) + timedelta(minutes=20), 9000, prefix="b"),
        ]
        tickets = project(events)
        self.assertEqual(tickets["t_a"].total_cents, 4000)
        self.assertEqual(tickets["t_b"].total_cents, 9000)
