from __future__ import annotations

import json
import unittest
from datetime import timedelta

from event_spine.events import EventType
from event_spine.outcome import OutcomeRow, by_outcome, outcome_of
from event_spine.project import project
from event_spine.report import render_outcome, render_outcome_json
from event_spine.simulate import SimConfig, simulate_day
from event_spine.stats import percentile
from tests.helpers import at, ev, ticket_flow


class OutcomeOfTests(unittest.TestCase):
    def test_classifies_payment_journeys(self) -> None:
        clean = project(ticket_flow("t_c", at(8), 4000, prefix="c"))["t_c"]
        self.assertEqual(outcome_of(clean), "clean")

        recovered = project(
            ticket_flow("t_r", at(9), 5000, prefix="r", fail=True)
        )["t_r"]
        self.assertEqual(outcome_of(recovered), "recovered")

        open_events = [
            ev(
                "o1",
                EventType.TICKET_OPENED,
                at(10),
                "t_o",
                bay="1",
                vehicle="2018 Honda Civic",
            ),
            ev(
                "o2",
                EventType.LINE_ITEM_ADDED,
                at(10, 1),
                "t_o",
                sku="OIL-CONV",
                description="oil",
                qty=1,
                unit_cents=3999,
            ),
        ]
        opened = project(open_events)["t_o"]
        self.assertEqual(outcome_of(opened), "open")

        unpaid_events = [
            ev(
                "u1",
                EventType.TICKET_OPENED,
                at(11),
                "t_u",
                bay="2",
                vehicle="2015 Ford F-150",
            ),
            ev(
                "u2",
                EventType.LINE_ITEM_ADDED,
                at(11, 1),
                "t_u",
                sku="OIL-SYN",
                description="oil",
                qty=1,
                unit_cents=6999,
            ),
            ev(
                "u3",
                EventType.PAYMENT_FAILED,
                at(11, 2),
                "t_u",
                method="card",
                amount_cents=6999,
                reason="declined",
            ),
            ev("u4", EventType.TICKET_CLOSED, at(11, 3), "t_u", total_cents=6999),
        ]
        unpaid = project(unpaid_events)["t_u"]
        self.assertEqual(outcome_of(unpaid), "unpaid")

    def test_missing_stays_empty(self) -> None:
        self.assertEqual(outcome_of(None), "")
        self.assertEqual(outcome_of(""), "")
        self.assertEqual(outcome_of(42), "")


class OutcomeFoldTests(unittest.TestCase):
    def test_groups_by_classified_outcome(self) -> None:
        unpaid_events = [
            ev(
                "u1",
                EventType.TICKET_OPENED,
                at(11),
                "t_u",
                bay="2",
                vehicle="2015 Ford F-150",
            ),
            ev(
                "u2",
                EventType.LINE_ITEM_ADDED,
                at(11, 1),
                "t_u",
                sku="OIL-SYN",
                description="oil",
                qty=1,
                unit_cents=6999,
            ),
            ev(
                "u3",
                EventType.PAYMENT_FAILED,
                at(11, 2),
                "t_u",
                method="card",
                amount_cents=6999,
                reason="declined",
            ),
            ev("u4", EventType.TICKET_CLOSED, at(11, 3), "t_u", total_cents=6999),
        ]
        events = [
            *ticket_flow(
                "t_a",
                at(8),
                4000,
                prefix="a",
                dwell=timedelta(minutes=30),
            ),
            *ticket_flow(
                "t_b",
                at(12),
                8000,
                prefix="b",
                fail=True,
                dwell=timedelta(minutes=60),
            ),
            *unpaid_events,
            ev(
                "o1",
                EventType.TICKET_OPENED,
                at(16),
                "t_open",
                bay="1",
                vehicle="2017 BMW 328i",
            ),
        ]
        rows = {row.outcome: row for row in by_outcome(events)}
        self.assertEqual(set(rows), {"clean", "recovered", "unpaid", "open"})

        clean = rows["clean"]
        self.assertEqual(clean.tickets, 1)
        self.assertEqual(clean.closed, 1)
        self.assertEqual(clean.open, 0)
        self.assertEqual(clean.revenue_cents, 4000)
        self.assertAlmostEqual(clean.dwell_p50_min or 0.0, 30.0)

        recovered = rows["recovered"]
        self.assertEqual(recovered.tickets, 1)
        self.assertEqual(recovered.closed, 1)
        self.assertEqual(recovered.open, 0)
        self.assertEqual(recovered.revenue_cents, 8000)
        self.assertAlmostEqual(recovered.dwell_p50_min or 0.0, 60.0)

        unpaid = rows["unpaid"]
        self.assertEqual(unpaid.tickets, 1)
        self.assertEqual(unpaid.closed, 1)
        self.assertEqual(unpaid.open, 0)
        self.assertEqual(unpaid.revenue_cents, 6999)

        opened = rows["open"]
        self.assertEqual(opened.tickets, 1)
        self.assertEqual(opened.closed, 0)
        self.assertEqual(opened.open, 1)
        self.assertEqual(opened.revenue_cents, 0)
        self.assertIsNone(opened.dwell_p50_min)

    def test_open_ticket_skips_revenue_and_dwell(self) -> None:
        events = [
            ev(
                "e1",
                EventType.TICKET_OPENED,
                at(8),
                "t1",
                bay="3",
                vehicle="2021 Toyota RAV4",
            ),
            ev(
                "e2",
                EventType.LINE_ITEM_ADDED,
                at(8, 1),
                "t1",
                sku="OIL-CONV",
                description="oil",
                qty=1,
                unit_cents=3999,
            ),
        ]
        rows = by_outcome(events)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].outcome, "open")
        self.assertEqual(rows[0].tickets, 1)
        self.assertEqual(rows[0].closed, 0)
        self.assertEqual(rows[0].open, 1)
        self.assertEqual(rows[0].revenue_cents, 0)
        self.assertIsNone(rows[0].dwell_p50_min)

    def test_empty_log_is_empty_list(self) -> None:
        self.assertEqual(by_outcome([]), [])

    def test_sorts_by_revenue_then_outcome_order(self) -> None:
        events = [
            *ticket_flow("t_z", at(8), 1000, prefix="z"),
            *ticket_flow("t_a", at(9), 1000, prefix="a"),
            *ticket_flow("t_m", at(12), 5000, prefix="m", fail=True),
        ]
        self.assertEqual(
            [row.outcome for row in by_outcome(events)],
            ["recovered", "clean"],
        )
        self.assertEqual(by_outcome(events)[1].tickets, 2)

    def test_tie_breaks_clean_recovered_unpaid_open(self) -> None:
        unpaid_events = [
            ev(
                "u1",
                EventType.TICKET_OPENED,
                at(11),
                "t_u",
                bay="2",
                vehicle="2015 Ford F-150",
            ),
            ev(
                "u2",
                EventType.LINE_ITEM_ADDED,
                at(11, 1),
                "t_u",
                sku="OIL-SYN",
                description="oil",
                qty=1,
                unit_cents=2000,
            ),
            ev(
                "u3",
                EventType.PAYMENT_FAILED,
                at(11, 2),
                "t_u",
                method="card",
                amount_cents=2000,
                reason="timeout",
            ),
            ev("u4", EventType.TICKET_CLOSED, at(11, 3), "t_u", total_cents=2000),
        ]
        events = [
            *ticket_flow("t_a", at(16), 2000, prefix="a", fail=True),
            *ticket_flow("t_m", at(8), 2000, prefix="m"),
            *unpaid_events,
            ev(
                "o1",
                EventType.TICKET_OPENED,
                at(17),
                "t_open",
                bay="1",
                vehicle="2017 BMW 328i",
            ),
        ]
        # open has 0 revenue so sorts after the three 2000 rows
        self.assertEqual(
            [row.outcome for row in by_outcome(events)],
            ["clean", "recovered", "unpaid", "open"],
        )

    def test_seeded_day_covers_all_tickets(self) -> None:
        events = simulate_day(SimConfig(seed=42))
        rows = by_outcome(events)
        tickets = project(events)
        self.assertEqual(sum(row.tickets for row in rows), len(tickets))
        expected = sum(t.total_cents for t in tickets.values() if t.closed)
        self.assertEqual(sum(row.revenue_cents for row in rows), expected)
        self.assertGreater(expected, 0)
        leftover = sum(1 for t in tickets.values() if not t.closed)
        self.assertEqual(sum(row.open for row in rows), leftover)
        names = {row.outcome for row in rows}
        self.assertIn("clean", names)
        self.assertNotIn("", names)

        by: dict[str, list] = {}
        for ticket in tickets.values():
            by.setdefault(outcome_of(ticket), []).append(ticket)
        manual: list[OutcomeRow] = []
        for outcome, group in by.items():
            closed = [t for t in group if t.closed]
            dwells = [
                (t.closed_at - t.opened_at).total_seconds() / 60.0
                for t in closed
                if t.closed_at is not None
            ]
            manual.append(
                OutcomeRow(
                    outcome=outcome,
                    tickets=len(group),
                    closed=len(closed),
                    open=len(group) - len(closed),
                    revenue_cents=sum(t.total_cents for t in closed),
                    dwell_p50_min=percentile(dwells, 0.50),
                )
            )
        order = {"clean": 0, "recovered": 1, "unpaid": 2, "open": 3}
        manual.sort(
            key=lambda row: (
                -row.revenue_cents,
                order.get(row.outcome, 99),
                row.outcome,
            )
        )
        self.assertEqual(rows, manual)


class OutcomeRenderTests(unittest.TestCase):
    def test_human_and_json_match_fold(self) -> None:
        events = [
            *ticket_flow("t_a", at(8), 6999, prefix="a"),
            *ticket_flow("t_b", at(16), 1299, prefix="b", fail=True),
        ]
        rows = by_outcome(events)
        text = render_outcome(events, rows)
        self.assertIn("outcome", text)
        self.assertIn("2 outcomes", text)
        self.assertIn("2 tickets", text)
        self.assertIn("clean", text)
        self.assertIn("recovered", text)
        payload = json.loads(render_outcome_json(events, rows))
        self.assertEqual(payload["events"], len(events))
        self.assertIn("shop", payload)
        self.assertIn("day", payload)
        self.assertIn("outcomes", payload)
        self.assertEqual(
            [
                (r["outcome"], r["tickets"], r["revenue_cents"])
                for r in payload["outcomes"]
            ],
            [(row.outcome, row.tickets, row.revenue_cents) for row in rows],
        )


if __name__ == "__main__":
    unittest.main()
