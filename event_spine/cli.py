"""simulate a day, detect anomalies, summarize the log, replay tickets, fold hours, or list silent gaps."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from event_spine import __version__
from event_spine.detect import detect
from event_spine.events import Event, EventType
from event_spine.project import project
from event_spine.report import (
    render_detect,
    render_detect_json,
    render_gaps,
    render_hours,
    render_replay,
    render_stats,
)
from event_spine.simulate import SimConfig, simulate_day
from event_spine.store import JsonlEventStore

DEFAULT_STORE = Path("data/events.jsonl")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="event_spine",
        description="Event-sourced POS log + statistical anomaly detectors.",
    )
    parser.add_argument("--version", action="version", version=f"event-spine {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sim = sub.add_parser("simulate", help="write a seeded shop day to a jsonl log")
    sim.add_argument("--out", type=Path, default=DEFAULT_STORE, help="jsonl path")
    sim.add_argument("--seed", type=int, default=42)

    det = sub.add_parser("detect", help="run detectors on a jsonl log")
    det.add_argument("--store", type=Path, default=DEFAULT_STORE)
    det.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="print anomalies as a JSON array",
    )

    st = sub.add_parser("stats", help="ticket count, fail rate, dwell percentiles, detector hits")
    st.add_argument("--store", type=Path, default=DEFAULT_STORE)

    hrs = sub.add_parser("hours", help="hourly tickets, payments, and revenue from the log")
    hrs.add_argument("--store", type=Path, default=DEFAULT_STORE)

    gps = sub.add_parser(
        "gaps",
        help="shop-hour stretches with no TicketOpened longer than 45 minutes",
    )
    gps.add_argument("--store", type=Path, default=DEFAULT_STORE)

    rep = sub.add_parser("replay", help="fold events into tickets and print them")
    rep.add_argument("--store", type=Path, default=DEFAULT_STORE)
    rep.add_argument("--limit", type=int, default=None)

    args = parser.parse_args(argv)
    if args.cmd == "simulate":
        return _simulate(args.out, args.seed)
    if args.cmd == "detect":
        return _detect(args.store, args.as_json)
    if args.cmd == "stats":
        return _stats(args.store)
    if args.cmd == "hours":
        return _hours(args.store)
    if args.cmd == "gaps":
        return _gaps(args.store)
    if args.cmd == "replay":
        return _replay(args.store, args.limit)
    return 2


def _simulate(path: Path, seed: int) -> int:
    events = simulate_day(SimConfig(seed=seed))
    if path.exists():
        path.unlink()
    store = JsonlEventStore(path)
    n = store.append_many(events)
    opened = sum(1 for e in events if e.type is EventType.TICKET_OPENED)
    failed = sum(1 for e in events if e.type is EventType.PAYMENT_FAILED)
    first, last = events[0].occurred_at, events[-1].occurred_at
    print(f"wrote {n} events → {path}")
    print(f"  tickets opened: {opened}")
    print(f"  payments failed: {failed}")
    print(f"  span: {first:%Y-%m-%d %H:%M} → {last:%H:%M} UTC")
    return 0


def _load(path: Path) -> list[Event]:
    if not path.exists():
        print(f"no event log at {path}", file=sys.stderr)
        print("run: python -m event_spine simulate", file=sys.stderr)
        return []
    return JsonlEventStore(path).load()


def _detect(path: Path, as_json: bool = False) -> int:
    events = _load(path)
    if not events:
        return 2
    anomalies = detect(events)
    if as_json:
        print(render_detect_json(anomalies), end="")
    else:
        print(render_detect(events, anomalies), end="")
    return 0


def _stats(path: Path) -> int:
    events = _load(path)
    if not events:
        return 2
    print(render_stats(events), end="")
    return 0


def _hours(path: Path) -> int:
    events = _load(path)
    if not events:
        return 2
    print(render_hours(events), end="")
    return 0


def _gaps(path: Path) -> int:
    events = _load(path)
    if not events:
        return 2
    print(render_gaps(events), end="")
    return 0


def _replay(path: Path, limit: int | None) -> int:
    events = _load(path)
    if not events:
        return 2
    print(render_replay(project(events), limit=limit), end="")
    return 0
