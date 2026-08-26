from __future__ import annotations

import json
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from event_spine.cli import main
from event_spine.simulate import SimConfig, simulate_day
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
            self.assertIn("ticket_total_mad", text)
            self.assertIn("payment_failure", text)
            self.assertIn("payment_failure_cusum", text)
            self.assertIn("payment_failure_ewma", text)
            self.assertIn("velocity", text)
            self.assertIn("ticket_dwell", text)
            self.assertIn("concurrent_open", text)
            self.assertIn("still open", text)
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
            self.assertIn("payment_failure_ewma", text)
            self.assertIn("ticket_total_mad", text)
            self.assertIn("detector hits", text)

            before = path.read_text(encoding="utf-8")
            with patch("sys.stdout", new=StringIO()) as out:
                code = main(["hours", "--store", str(path)])
            text = out.getvalue()
            self.assertEqual(code, 0)
            self.assertEqual(path.read_text(encoding="utf-8"), before)
            self.assertIn("hourly", text)
            self.assertIn("opened", text)
            self.assertIn("captured", text)
            self.assertIn("failed", text)
            self.assertIn("revenue $", text)
            self.assertIn("07:00", text)
            self.assertIn("16:00", text)

            before = path.read_text(encoding="utf-8")
            with patch("sys.stdout", new=StringIO()) as out:
                code = main(["brief", "--store", str(path)])
            text = out.getvalue()
            self.assertEqual(code, 0)
            self.assertEqual(path.read_text(encoding="utf-8"), before)
            self.assertIn("daily brief", text)
            self.assertIn("opened", text)
            self.assertIn("closed", text)
            self.assertIn("leftover open", text)
            self.assertIn("captured", text)
            self.assertIn("failed", text)
            self.assertIn("revenue $", text)
            self.assertIn("detector hits", text)
            self.assertIn("payment_failure_cusum", text)
            self.assertIn("payment_failure_ewma", text)
            self.assertIn("ticket_dwell", text)
            self.assertIn("ticket_total_mad", text)
            self.assertIn("ticket_total_iqr", text)
            self.assertIn("silent_gap", text)

            before = path.read_text(encoding="utf-8")
            with patch("sys.stdout", new=StringIO()) as out:
                code = main(["gaps", "--store", str(path)])
            text = out.getvalue()
            self.assertEqual(code, 0)
            self.assertEqual(path.read_text(encoding="utf-8"), before)
            self.assertIn("silent gaps", text)
            self.assertIn("45min", text)
            self.assertIn("no silent gaps", text)

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

    def test_stats_json_object(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "day.jsonl"
            with patch("sys.stdout", new=StringIO()):
                self.assertEqual(main(["simulate", "--out", str(path), "--seed", "42"]), 0)
            before = path.read_text(encoding="utf-8")
            with patch("sys.stdout", new=StringIO()) as out:
                code = main(["stats", "--store", str(path), "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(path.read_text(encoding="utf-8"), before)
            payload = json.loads(out.getvalue())
            self.assertIsInstance(payload, dict)
            for key in (
                "shop",
                "day",
                "events",
                "tickets",
                "closed",
                "payments",
                "failures",
                "fail_rate",
                "dwell_p50_min",
                "dwell_p95_min",
                "detector_hits",
            ):
                self.assertIn(key, payload)
            self.assertGreater(payload["tickets"], 0)
            self.assertGreaterEqual(payload["fail_rate"], 0.0)
            self.assertIsInstance(payload["dwell_p50_min"], float)
            names = {row["detector"] for row in payload["detector_hits"]}
            self.assertIn("silent_gap", names)
            self.assertIn("ticket_dwell", names)
            for row in payload["detector_hits"]:
                self.assertEqual({"detector", "count"}, set(row.keys()))

    def test_hours_missing_store(self) -> None:
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.jsonl"
            with patch("sys.stderr", new=StringIO()) as err:
                code = main(["hours", "--store", str(missing)])
            self.assertEqual(code, 2)
            self.assertIn("no event log", err.getvalue())

    def test_hours_json_object(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "day.jsonl"
            with patch("sys.stdout", new=StringIO()):
                self.assertEqual(main(["simulate", "--out", str(path), "--seed", "42"]), 0)
            before = path.read_text(encoding="utf-8")
            with patch("sys.stdout", new=StringIO()) as out:
                code = main(["hours", "--store", str(path), "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(path.read_text(encoding="utf-8"), before)
            payload = json.loads(out.getvalue())
            self.assertIsInstance(payload, dict)
            self.assertIn("hours", payload)
            self.assertGreater(len(payload["hours"]), 0)
            first = payload["hours"][0]
            for key in (
                "hour",
                "tickets_opened",
                "payments_captured",
                "payments_failed",
                "revenue_cents",
                "peak_open",
            ):
                self.assertIn(key, first)
            self.assertTrue(first["hour"].endswith("+00:00") or "T" in first["hour"])
            self.assertGreaterEqual(sum(row["tickets_opened"] for row in payload["hours"]), 1)

    def test_brief_missing_store(self) -> None:
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.jsonl"
            with patch("sys.stderr", new=StringIO()) as err:
                code = main(["brief", "--store", str(missing)])
            self.assertEqual(code, 2)
            self.assertIn("no event log", err.getvalue())
            self.assertIn("simulate", err.getvalue())

    def test_brief_empty_store_prints_empty_brief(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.jsonl"
            path.write_text("", encoding="utf-8")
            before = path.read_text(encoding="utf-8")
            with patch("sys.stdout", new=StringIO()) as out:
                code = main(["brief", "--store", str(path)])
            text = out.getvalue()
            self.assertEqual(code, 0)
            self.assertEqual(path.read_text(encoding="utf-8"), before)
            self.assertIn("daily brief", text)
            self.assertIn("opened 0", text)
            self.assertIn("leftover open 0", text)
            self.assertIn("revenue $0.00", text)
            self.assertIn("detector hits", text)

    def test_gaps_json_object(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "day.jsonl"
            events = simulate_day(
                SimConfig(
                    seed=42,
                    silent_gap_hour=12,
                    silent_gap_minute=10,
                    silent_gap_minutes=50,
                )
            )
            JsonlEventStore(path).append_many(events)
            before = path.read_text(encoding="utf-8")
            with patch("sys.stdout", new=StringIO()) as out:
                code = main(["gaps", "--store", str(path), "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(path.read_text(encoding="utf-8"), before)
            payload = json.loads(out.getvalue())
            self.assertIsInstance(payload, dict)
            self.assertIn("gaps", payload)
            self.assertGreaterEqual(len(payload["gaps"]), 1)
            self.assertEqual(payload["threshold_minutes"], 45)
            first = payload["gaps"][0]
            for key in (
                "detector",
                "score",
                "at",
                "summary",
                "event_ids",
                "ticket_id",
                "details",
            ):
                self.assertIn(key, first)
            self.assertEqual(first["detector"], "silent_gap")
            self.assertTrue(first["at"].endswith("+00:00") or "T" in first["at"])

    def test_gaps_missing_store(self) -> None:
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.jsonl"
            with patch("sys.stderr", new=StringIO()) as err:
                code = main(["gaps", "--store", str(missing)])
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
            self.assertIn("payment_failure_ewma", names)
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

    def test_brief_json_object(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "day.jsonl"
            with patch("sys.stdout", new=StringIO()):
                self.assertEqual(main(["simulate", "--out", str(path), "--seed", "42"]), 0)
            before = path.read_text(encoding="utf-8")
            with patch("sys.stdout", new=StringIO()) as out:
                code = main(["brief", "--store", str(path), "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(path.read_text(encoding="utf-8"), before)
            payload = json.loads(out.getvalue())
            self.assertIsInstance(payload, dict)
            for key in (
                "shop",
                "day",
                "events",
                "tickets_opened",
                "tickets_closed",
                "payments_captured",
                "payments_failed",
                "revenue_cents",
                "leftover",
                "detector_hits",
            ):
                self.assertIn(key, payload)
            self.assertIsInstance(payload["leftover"], list)
            self.assertIsInstance(payload["detector_hits"], list)
            self.assertGreater(payload["tickets_opened"], 0)
            self.assertGreaterEqual(payload["revenue_cents"], 0)
            names = {row["detector"] for row in payload["detector_hits"]}
            self.assertIn("silent_gap", names)
            self.assertIn("ticket_total_iqr", names)
            for row in payload["detector_hits"]:
                self.assertEqual({"detector", "count"}, set(row.keys()))
                self.assertIsInstance(row["count"], int)

    def test_brief_json_empty_store(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.jsonl"
            path.write_text("", encoding="utf-8")
            with patch("sys.stdout", new=StringIO()) as out:
                code = main(["brief", "--store", str(path), "--json"])
            self.assertEqual(code, 0)
            payload = json.loads(out.getvalue())
            self.assertIsNone(payload["day"])
            self.assertEqual(payload["events"], 0)
            self.assertEqual(payload["tickets_opened"], 0)
            self.assertEqual(payload["leftover"], [])
            self.assertEqual(payload["revenue_cents"], 0)

    def test_gaps_flags_planted_lunch_silence(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "day.jsonl"
            events = simulate_day(
                SimConfig(
                    seed=42,
                    silent_gap_hour=12,
                    silent_gap_minute=10,
                    silent_gap_minutes=50,
                )
            )
            JsonlEventStore(path).append_many(events)
            with patch("sys.stdout", new=StringIO()) as out:
                code = main(["gaps", "--store", str(path)])
            text = out.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("TicketOpened", text)
            self.assertNotIn("no silent gaps", text)
            with patch("sys.stdout", new=StringIO()) as out:
                code = main(["detect", "--store", str(path)])
            self.assertEqual(code, 0)
            self.assertIn("silent_gap", out.getvalue())

    def test_replay_json_object(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "day.jsonl"
            with patch("sys.stdout", new=StringIO()):
                self.assertEqual(main(["simulate", "--out", str(path), "--seed", "42"]), 0)
            before = path.read_text(encoding="utf-8")
            with patch("sys.stdout", new=StringIO()) as out:
                code = main(["replay", "--store", str(path), "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(path.read_text(encoding="utf-8"), before)
            payload = json.loads(out.getvalue())
            self.assertIsInstance(payload, dict)
            self.assertIn("tickets", payload)
            self.assertGreater(len(payload["tickets"]), 0)
            first = payload["tickets"][0]
            for key in (
                "ticket_id",
                "opened_at",
                "closed_at",
                "bay",
                "vehicle",
                "total_cents",
                "paid",
                "closed",
                "items",
                "payments",
            ):
                self.assertIn(key, first)
            self.assertIsInstance(first["items"], list)
            self.assertIsInstance(first["payments"], list)
            self.assertIsInstance(first["total_cents"], int)
            ids = [row["ticket_id"] for row in payload["tickets"]]
            self.assertIn("t_001", ids)
            with patch("sys.stdout", new=StringIO()) as out:
                code = main(["replay", "--store", str(path), "--json", "--limit", "3"])
            self.assertEqual(code, 0)
            limited = json.loads(out.getvalue())
            self.assertEqual(len(limited["tickets"]), 3)

