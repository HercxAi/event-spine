# event-spine

A day's worth of a fictional quick-lube shop, stored as events, then
watched by seven detectors that use actual statistics.

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
python -m event_spine stats
python -m event_spine hours
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

No model weights. Seven checks with named math:

1. **Ticket total z-score.** After each close, compare the ticket's
   line-item sum to the previous N tickets. Sample standard deviation
   (Bessel, n−1), high side only. Catches the $565 flush ticket
   against an oil-change baseline.

2. **Payment-failure burst.** Sliding time window over
   `PaymentCaptured` / `PaymentFailed`. One-sample proportion z-test
   against the morning baseline. Overlapping windows collapse to the
   peak. Catches a terminal that starts declining everything at 4pm.

3. **Payment-failure CUSUM.** Same payment stream, scored 1 on fail
   and 0 on capture. Morning Bernoulli mean p0 from payments before
   14:00 UTC (the 16:03 plant stays out of the baseline). High-side
   tabular CUSUM (Page 1954 / Montgomery): S_t = max(0, S_{t−1} + x_t − k)
   with slack k = p0 + ½σ, σ = √(p0(1−p0)), alarm at h = 4. One
   change-point per excursion, not a hit on every later fail. The
   proportion-z burst and this CUSUM both fire on the planted outage;
   one is a windowed rate, the other is a sequential change-point.

4. **Payment-failure EWMA.** Same stream, same morning p0. Roberts
   (1959) EWMA: Z_t = λ x_t + (1−λ) Z_{t−1}, Z_0 = p0, λ = 0.1.
   Asymptotic UCL = p0 + L·σ·√(λ/(2−λ)), L = 3, σ = √(p0(1−p0)).
   One change-point per excursion — latches until Z returns to p0.
   A slow rise in the decline rate moves Z before CUSUM's S reaches
   h; the 16:03 plant still fires, usually a payment or two earlier.

5. **Velocity spike.** `TicketOpened` counts in fixed 5-minute bins,
   z-score versus the previous bins. Empty bins count — otherwise a
   quiet shop looks busy. Catches a fleet that dumps eight cars on
   the lot at once.

6. **Ticket dwell time.** After each close, the minutes between
   `TicketOpened` and `TicketClosed` versus the previous N closed
   tickets. Same sample z-score, high side only. Tickets that never
   closed are scored the same way against the last timestamp in the
   log, so a bay that never closed still shows up. Catches a bay that
   sits on one car for hours — including the unpaid outage ticket
   still sitting in a bay at close.

7. **Concurrent open tickets.** Walk the log; increment on
   `TicketOpened`, decrement on `TicketClosed`. After each, compare
   the live count to the previous N snapshots. Same sample z-score,
   high side only. Overlapping snapshots of the same cars collapse
   to the fullest lot. Catches a pile-up — the shop holding far more
   cars at once than the recent baseline.

Each anomaly prints the score, the window or ticket, and the event ids
that justify it. `detect --json` emits the same list as a JSON array.

`stats` folds the same log into a one-screen summary: ticket count,
payment-failure rate, p50/p95 dwell (linear interpolation, Hyndman-Fan
type 7), and how many times each detector fired.

`hours` folds the same log into one line per shop-open hour (07:00–18:00
UTC, plus any hour that actually has events). Empty hours stay in the
table — a quiet mid-morning is a fact, not a missing row. Each line is
tickets opened, payments captured vs failed, revenue from captured
`amount_cents` (integer cents, printed as dollars), and peak concurrent
open tickets in that hour, including cars still sitting from earlier.

## 2026-08-21

Roberts EWMA on the payment-failure stream (λ = 0.1, L = 3):
Z_t = λx_t + (1−λ)Z_{t−1}, alarm at the asymptotic UCL. Same 16:03
card-terminal plant as the proportion-z burst and CUSUM; this one is
a slow-burn smoother — it moves before CUSUM trips.

`hours` command: rebuild an hourly shop view from the append-only log.

High-side tabular CUSUM on the payment stream (Page / Montgomery:
k = p0 + ½σ, h = 4). Same 16:03 card-terminal plant as the
proportion-z burst; this one is a sequential change-point.

Dwell detector also scores tickets that never closed, as of the last event.

## 2026-08-20

`stats` command: ticket count, fail rate, dwell percentiles, detector hits.
Concurrent open-ticket detector (shop load): running count versus a
rolling sample z-score, high side only.

## Layout

```
event_spine/events.py     fact types + jsonl codec
event_spine/store.py      append-only store
event_spine/project.py    fold events → tickets
event_spine/simulate.py   seeded day generator
event_spine/detect.py     the seven checks
event_spine/stats.py      day summary + percentiles
event_spine/hours.py      hourly fold from the log
event_spine/report.py     stdout
event_spine/cli.py        simulate | detect | stats | hours | replay
```

## License

MIT © 2026 Jeff Olfert
