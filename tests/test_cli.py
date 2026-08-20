from __future__ import annotations

import json
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from event_spine.cli import main
from event_spine.detect import detect, fmt_cents
from event_spine.events import EventType
from event_spine.project import project
from event_spine.store import JsonlEventStore


class CliTests(unittest.TestCase):
    def test_simulate_then_detect(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "day.jsonl"
            with patch("sys.stdout", new=StringIO()) as out:
                code = main(["simulate", "--out", str(path), "--seed", "42"])
            self.assertEqual(code, 0)
            self.assertTrue(path.exists())
            self.assertIn("wrote", out.getvalue())
            self.assertGreater(path.stat().st_size, 0)

            with patch("sys.stdout", new=StringIO()) as out:
                code = main(["detect", "--store", str(path)])
            text = out.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("ticket_total", text)
            self.assertIn("payment_failure", text)
            self.assertIn("velocity", text)
            self.assertIn("ticket_dwell", text)
            self.assertIn("events:", text)

            with patch("sys.stdout", new=StringIO()) as out:
                code = main(["replay", "--store", str(path), "--limit", "3"])
            self.assertEqual(code, 0)
            self.assertIn("t_001", out.getvalue())

    def test_detect_missing_store(self) -> None:
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.jsonl"
            with patch("sys.stderr", new=StringIO()) as err:
                code = main(["detect", "--store", str(missing)])
            self.assertEqual(code, 2)
            self.assertIn("no event log", err.getvalue())

    def test_simulate_replaces_file_store_only_appends(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "day.jsonl"
            with patch("sys.stdout", new=StringIO()):
                self.assertEqual(main(["simulate", "--out", str(path), "--seed", "42"]), 0)
            first = path.read_text(encoding="utf-8")
            with patch("sys.stdout", new=StringIO()):
                self.assertEqual(main(["simulate", "--out", str(path), "--seed", "42"]), 0)
            self.assertEqual(path.read_text(encoding="utf-8"), first)

    def test_detect_json_array(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "day.jsonl"
            with patch("sys.stdout", new=StringIO()):
                self.assertEqual(main(["simulate", "--out", str(path), "--seed", "42"]), 0)
            with patch("sys.stdout", new=StringIO()) as out:
                code = main(["detect", "--store", str(path), "--json"])
            self.assertEqual(code, 0)
            payload = json.loads(out.getvalue())
            self.assertIsInstance(payload, list)
            self.assertGreaterEqual(len(payload), 1)
            names = {row["detector"] for row in payload}
            self.assertIn("ticket_dwell", names)
            required = {
                "detector",
                "score",
                "at",
                "summary",
                "event_ids",
                "ticket_id",
                "details",
            }
            for row in payload:
                self.assertEqual(required, set(row.keys()) & required)
                self.assertTrue(row["at"].endswith("+00:00") or "T" in row["at"])
                self.assertIsInstance(row["event_ids"], list)
                self.assertIsInstance(row["details"], dict)

    def test_summary_seeded_day(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "day.jsonl"
            with patch("sys.stdout", new=StringIO()):
                self.assertEqual(main(["simulate", "--out", str(path), "--seed", "42"]), 0)

            events = JsonlEventStore(path).load()
            tickets = project(events)
            closed = [t for t in tickets.values() if t.closed]
            revenue = sum(t.total_cents for t in closed)
            captured = sum(1 for e in events if e.type is EventType.PAYMENT_CAPTURED)
            failed = sum(1 for e in events if e.type is EventType.PAYMENT_FAILED)
            anomalies = detect(events)
            self.assertGreaterEqual(len(anomalies), 1)

            with patch("sys.stdout", new=StringIO()) as out:
                code = main(["summary", "--store", str(path)])
            text = out.getvalue()
            self.assertEqual(code, 0)
            self.assertIn(f"tickets   {len(tickets)}", text)
            self.assertIn(f"revenue   {fmt_cents(revenue)}", text)
            self.assertIn(f"payments  {captured} captured / {failed} failed", text)
            self.assertTrue(text.lstrip().startswith("Splitrock Lube"))

            names = {a.detector for a in anomalies}
            for name in names:
                self.assertIn(name, text)
            for anomaly in anomalies:
                self.assertIn(anomaly.summary, text)

    def test_summary_missing_store(self) -> None:
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.jsonl"
            with patch("sys.stderr", new=StringIO()) as err:
                code = main(["summary", "--store", str(missing)])
            self.assertEqual(code, 2)
            self.assertIn("no event log", err.getvalue())
