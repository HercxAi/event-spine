"""simulate a day, detect anomalies, summarize the log, print a daily brief, replay tickets, fold hours, list silent gaps, fold SKUs, fold families, fold bays, fold dwell, fold size, fold lines, fold vehicles, or fold payment methods."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from event_spine import __version__
from event_spine.detect import detect
from event_spine.events import Event, EventType
from event_spine.project import project
from event_spine.report import (
    render_bay,
    render_bay_json,
    render_brief,
    render_brief_json,
    render_detect,
    render_detect_json,
    render_dwell,
    render_dwell_json,
    render_gaps,
    render_gaps_json,
    render_hours,
    render_hours_json,
    render_pay,
    render_pay_json,
    render_reason,
    render_reason_json,
    render_replay,
    render_replay_json,
    render_size,
    render_size_json,
    render_lines,
    render_lines_json,
    render_family,
    render_family_json,
    render_sku,
    render_sku_json,
    render_stats,
    render_stats_json,
    render_vehicle,
    render_vehicle_json,
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
    st.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="print the summary as a JSON object",
    )

    hrs = sub.add_parser("hours", help="hourly tickets, payments, and revenue from the log")
    hrs.add_argument("--store", type=Path, default=DEFAULT_STORE)
    hrs.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="print the hourly fold as a JSON object",
    )

    br = sub.add_parser("brief", help="one-page daily ops brief rebuilt from the log")
    br.add_argument("--store", type=Path, default=DEFAULT_STORE)
    br.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="print the brief as a JSON object",
    )

    gps = sub.add_parser(
        "gaps",
        help="shop-hour stretches with no TicketOpened longer than 45 minutes",
    )
    gps.add_argument("--store", type=Path, default=DEFAULT_STORE)
    gps.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="print the silent-gap fold as a JSON object",
    )

    sk = sub.add_parser(
        "sku",
        help="SKU lines, qty, and ext cents rebuilt from LineItemAdded",
    )
    sk.add_argument("--store", type=Path, default=DEFAULT_STORE)
    sk.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="print the SKU fold as a JSON object",
    )

    fm = sub.add_parser(
        "family",
        help="catalog-family lines, qty, and ext cents rebuilt from LineItemAdded",
    )
    fm.add_argument("--store", type=Path, default=DEFAULT_STORE)
    fm.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="print the family fold as a JSON object",
    )

    by = sub.add_parser(
        "bay",
        help="per-bay tickets, revenue, and dwell rebuilt from the ticket projection",
    )
    by.add_argument("--store", type=Path, default=DEFAULT_STORE)
    by.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="print the bay fold as a JSON object",
    )

    dw = sub.add_parser(
        "dwell",
        help="closed-ticket dwell bands rebuilt from open/close facts",
    )
    dw.add_argument("--store", type=Path, default=DEFAULT_STORE)
    dw.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="print the dwell-bucket fold as a JSON object",
    )

    vh = sub.add_parser(
        "vehicle",
        help="per-vehicle tickets, revenue, and dwell rebuilt from the ticket projection",
    )
    vh.add_argument("--store", type=Path, default=DEFAULT_STORE)
    vh.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="print the vehicle fold as a JSON object",
    )


    ln = sub.add_parser(
        "lines",
        help="closed-ticket line-count bands rebuilt from the ticket projection",
    )
    ln.add_argument("--store", type=Path, default=DEFAULT_STORE)
    ln.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="print the line-count band fold as a JSON object",
    )

    sz = sub.add_parser(
        "size",
        help="closed-ticket total bands rebuilt from line-item sums",
    )
    sz.add_argument("--store", type=Path, default=DEFAULT_STORE)
    sz.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="print the ticket-total band fold as a JSON object",
    )

    rs = sub.add_parser(
        "reason",
        help="PaymentFailed reasons, ask cents, and methods rebuilt from the log",
    )
    rs.add_argument("--store", type=Path, default=DEFAULT_STORE)
    rs.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="print the failure-reason fold as a JSON object",
    )
    py = sub.add_parser(
        "pay",
        help="per-method captured vs failed rebuilt from payment events",
    )
    py.add_argument("--store", type=Path, default=DEFAULT_STORE)
    py.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="print the payment-method fold as a JSON object",
    )

    rep = sub.add_parser("replay", help="fold events into tickets and print them")
    rep.add_argument("--store", type=Path, default=DEFAULT_STORE)
    rep.add_argument("--limit", type=int, default=None)
    rep.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="print the ticket projection as a JSON object",
    )

    args = parser.parse_args(argv)
    if args.cmd == "simulate":
        return _simulate(args.out, args.seed)
    if args.cmd == "detect":
        return _detect(args.store, args.as_json)
    if args.cmd == "stats":
        return _stats(args.store, args.as_json)
    if args.cmd == "hours":
        return _hours(args.store, args.as_json)
    if args.cmd == "brief":
        return _brief(args.store, args.as_json)
    if args.cmd == "gaps":
        return _gaps(args.store, args.as_json)
    if args.cmd == "sku":
        return _sku(args.store, args.as_json)
    if args.cmd == "family":
        return _family(args.store, args.as_json)
    if args.cmd == "bay":
        return _bay(args.store, args.as_json)
    if args.cmd == "dwell":
        return _dwell(args.store, args.as_json)
    if args.cmd == "vehicle":
        return _vehicle(args.store, args.as_json)
    if args.cmd == "lines":
        return _lines(args.store, args.as_json)
    if args.cmd == "size":
        return _size(args.store, args.as_json)
    if args.cmd == "reason":
        return _reason(args.store, args.as_json)
    if args.cmd == "pay":
        return _pay(args.store, args.as_json)
    if args.cmd == "replay":
        return _replay(args.store, args.limit, args.as_json)
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


def _stats(path: Path, as_json: bool = False) -> int:
    events = _load(path)
    if not events:
        return 2
    if as_json:
        print(render_stats_json(events), end="")
    else:
        print(render_stats(events), end="")
    return 0


def _hours(path: Path, as_json: bool = False) -> int:
    events = _load(path)
    if not events:
        return 2
    if as_json:
        print(render_hours_json(events), end="")
    else:
        print(render_hours(events), end="")
    return 0


def _brief(path: Path, as_json: bool = False) -> int:
    events = _load(path)
    if not path.exists():
        return 2
    if as_json:
        print(render_brief_json(events), end="")
    else:
        print(render_brief(events), end="")
    return 0


def _gaps(path: Path, as_json: bool = False) -> int:
    events = _load(path)
    if not events:
        return 2
    if as_json:
        print(render_gaps_json(events), end="")
    else:
        print(render_gaps(events), end="")
    return 0


def _sku(path: Path, as_json: bool = False) -> int:
    events = _load(path)
    if not events:
        return 2
    if as_json:
        print(render_sku_json(events), end="")
    else:
        print(render_sku(events), end="")
    return 0


def _family(path: Path, as_json: bool = False) -> int:
    events = _load(path)
    if not events:
        return 2
    if as_json:
        print(render_family_json(events), end="")
    else:
        print(render_family(events), end="")
    return 0


def _bay(path: Path, as_json: bool = False) -> int:
    events = _load(path)
    if not events:
        return 2
    if as_json:
        print(render_bay_json(events), end="")
    else:
        print(render_bay(events), end="")
    return 0



def _dwell(path: Path, as_json: bool = False) -> int:
    events = _load(path)
    if not events:
        return 2
    if as_json:
        print(render_dwell_json(events), end="")
    else:
        print(render_dwell(events), end="")
    return 0




def _lines(path: Path, as_json: bool = False) -> int:
    events = _load(path)
    if not events:
        return 2
    if as_json:
        print(render_lines_json(events), end="")
    else:
        print(render_lines(events), end="")
    return 0

def _size(path: Path, as_json: bool = False) -> int:
    events = _load(path)
    if not events:
        return 2
    if as_json:
        print(render_size_json(events), end="")
    else:
        print(render_size(events), end="")
    return 0

def _vehicle(path: Path, as_json: bool = False) -> int:
    events = _load(path)
    if not events:
        return 2
    if as_json:
        print(render_vehicle_json(events), end="")
    else:
        print(render_vehicle(events), end="")
    return 0


def _reason(path: Path, as_json: bool = False) -> int:
    events = _load(path)
    if not events:
        return 2
    if as_json:
        print(render_reason_json(events), end="")
    else:
        print(render_reason(events), end="")
    return 0

def _pay(path: Path, as_json: bool = False) -> int:
    events = _load(path)
    if not events:
        return 2
    if as_json:
        print(render_pay_json(events), end="")
    else:
        print(render_pay(events), end="")
    return 0


def _replay(path: Path, limit: int | None, as_json: bool = False) -> int:
    events = _load(path)
    if not events:
        return 2
    tickets = project(events)
    if as_json:
        print(render_replay_json(tickets, events=events, limit=limit), end="")
    else:
        print(render_replay(tickets, limit=limit), end="")
    return 0
