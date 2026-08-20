# event-spine

A day's worth of a fictional quick-lube shop, stored as events, then
watched by four detectors that use actual statistics.

Inspired by production event-sourced POS/analytics work. This is not
that system. It is a small, honest demo of the same shape: append-only
facts, projections you can rebuild, and anomaly checks that do not
pretend to be a neural net.

**Jeff Olfert** · [github.com/jeffolfert](https://github.com/jeffolfert)

## Why a log, not a row

A ticket total is not a cell you overwrite. It is the sum of
`LineItemAdded`. A failed charge is not a status flag; it is a fact at
16:04:12. Store the facts and you can rebuild any view — including ones
you did not plan for.

That is the whole trick. The rest is a shop, a generator, and some
z-scores.

## The shop

Splitrock Lube, 14 March 2026. One fictional day: oil, filters, a few
wipers, a card terminal that has a bad afternoon.

```
TicketOpened → LineItemAdded+ → PaymentCaptured | PaymentFailed → TicketClosed
```

Events live in JSONL. The store only appends. `simulate` starts a new
day by replacing the file; history inside the store is immutable.

Money is integer cents. Times are UTC. Event ids are sequential so a
report is readable (`e_0287`, not a UUID you have to squint at).

## Run

Python 3.11+. Stdlib only.

```bash
python -m event_spine simulate
python -m event_spine detect
python -m event_spine detect --json
python -m event_spine replay --limit 5
```

```bash
python -m unittest discover -s tests
```

`simulate` is seeded (default 42). Same seed, same day. The generator
plants four irregularities — a bay that sits on one car for three hours
from 09:42, a fleet dump at 11:30, a whale ticket around 14:18, a
card-terminal sulk at 16:03 — and the detectors have to find them from
the log alone.

## Detectors

No model weights. Four checks with named math:

1. **Ticket total z-score.** After each close, compare the ticket's
   line-item sum to the previous N tickets. Sample standard deviation
   (Bessel, n−1), high side only. Catches the $565 flush ticket
   against an oil-change baseline.

2. **Payment-failure burst.** Sliding time window over
   `PaymentCaptured` / `PaymentFailed`. One-sample proportion z-test
   against the morning baseline. Overlapping windows collapse to the
   peak. Catches a terminal that starts declining everything at 4pm.

3. **Velocity spike.** `TicketOpened` counts in fixed 5-minute bins,
   z-score versus the previous bins. Empty bins count — otherwise a
   quiet shop looks busy. Catches a fleet that dumps eight cars on
   the lot at once.

4. **Ticket dwell time.** After each close, the minutes between
   `TicketOpened` and `TicketClosed` versus the previous N closed
   tickets. Same sample z-score, high side only. Catches a bay that
   sits on one car for hours.

Each anomaly prints the score, the window or ticket, and the event ids
that justify it. `detect --json` emits the same list as a JSON array.

## 2026-08-20

Ticket dwell-time detector, and JSON output on `detect --json`.

## Layout

```
event_spine/events.py     fact types + jsonl codec
event_spine/store.py      append-only store
event_spine/project.py    fold events → tickets
event_spine/simulate.py   seeded day generator
event_spine/detect.py     the four checks
event_spine/report.py     stdout
event_spine/cli.py        simulate | detect | replay
```

## License

MIT © 2026 Jeff Olfert
