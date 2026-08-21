from __future__ import annotations

import json
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from event_spine.cli import main


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
            self.assertIn("payment_failure_cusum", text)
            self.assertIn("velocity", text)
            self.assertIn("ticket_dwell", text)
            self.assertIn("concurrent_open", text)
            self.assertIn("events:", text)

            with patch("sys.stdout", new=StringIO()) as out:
                code = main(["replay", "--store", str(path), "--limit", "3"])
            self.assertEqual(code, 0)
            self.assertIn("t_001", out.getvalue())

            with patch("sys.stdout", new=StringIO()) as out:
                code = main(["stats", "--store", str(path)])
            text = out.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("tickets", text)
            self.assertIn("fail rate", text)
            self.assertIn("p50", text)
            self.assertIn("p95", text)
            self.assertIn("ticket_dwell", text)
            self.assertIn("concurrent_open", text)
            self.assertIn("payment_failure_cusum", text)
            self.assertIn("detector hits", text)

    def test_detect_missing_store(self) -> None:
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.jsonl"
            with patch("sys.stderr", new=StringIO()) as err:
                code = main(["detect", "--store", str(missing)])
            self.assertEqual(code, 2)
            self.assertIn("no event log", err.getvalue())

    def test_stats_missing_store(self) -> None:
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.jsonl"
            with patch("sys.stderr", new=StringIO()) as err:
                code = main(["stats", "--store", str(missing)])
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
            self.assertIn("concurrent_open", names)
            self.assertIn("payment_failure_cusum", names)
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
