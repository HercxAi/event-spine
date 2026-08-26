# event-spine

A day's worth of a fictional quick-lube shop, stored as events, then
watched by ten detectors that use actual statistics.

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
python -m event_spine brief
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

No model weights. Ten checks with named math:

1. **Ticket total z-score.** After each close, compare the ticket's
   line-item sum to the previous N tickets. Sample standard deviation
   (Bessel, n−1), high side only. Catches the $565 flush ticket
   against an oil-change baseline.

2. **Ticket total MAD.** Same closed totals, scored with the
   Iglewicz-Hoaglin (1993) modified z-score: M = 0.6745 (x − median) / MAD
   against the previous N tickets, high side only, alarm at 3.5. A prior
   whale inflates sample σ and can mask the next one; the median absolute
   deviation does not. Both fire on the planted flush ticket.

3. **Payment-failure burst.** Sliding time window over
   `PaymentCaptured` / `PaymentFailed`. One-sample proportion z-test
   against the morning baseline. Overlapping windows collapse to the
   peak. Catches a terminal that starts declining everything at 4pm.

4. **Payment-failure CUSUM.** Same payment stream, scored 1 on fail
   and 0 on capture. Morning Bernoulli mean p0 from payments before
   14:00 UTC (the 16:03 plant stays out of the baseline). High-side
   tabular CUSUM (Page 1954 / Montgomery): S_t = max(0, S_{t−1} + x_t − k)
   with slack k = p0 + ½σ, σ = √(p0(1−p0)), alarm at h = 4. One
   change-point per excursion, not a hit on every later fail. The
   proportion-z burst and this CUSUM both fire on the planted outage;
   one is a windowed rate, the other is a sequential change-point.

5. **Payment-failure EWMA.** Same stream, same morning p0. Roberts
   (1959) EWMA: Z_t = λ x_t + (1−λ) Z_{t−1}, Z_0 = p0, λ = 0.1.
   Asymptotic UCL = p0 + L·σ·√(λ/(2−λ)), L = 3, σ = √(p0(1−p0)).
   One change-point per excursion — latches until Z returns to p0.
   A slow rise in the decline rate moves Z before CUSUM's S reaches
   h; the 16:03 plant still fires, usually a payment or two earlier.

6. **Velocity spike.** `TicketOpened` counts in fixed 5-minute bins,
   z-score versus the previous bins. Empty bins count — otherwise a
   quiet shop looks busy. Catches a fleet that dumps eight cars on
   the lot at once.

7. **Ticket dwell time.** After each close, the minutes between
   `TicketOpened` and `TicketClosed` versus the previous N closed
   tickets. Same sample z-score, high side only. Tickets that never
   closed are scored the same way against the last timestamp in the
   log, so a bay that never closed still shows up. Catches a bay that
   sits on one car for hours — including the unpaid outage ticket
   still sitting in a bay at close.

8. **Concurrent open tickets.** Walk the log; increment on
   `TicketOpened`, decrement on `TicketClosed`. After each, compare
   the live count to the previous N snapshots. Same sample z-score,
   high side only. Overlapping snapshots of the same cars collapse
   to the fullest lot. Catches a pile-up — the shop holding far more
   cars at once than the recent baseline.

9. **Ticket total Tukey fence.** Same closed totals, scored with
   Tukey's inner fence: (x − Q3) / IQR against the previous N
   tickets, high side only, alarm at 1.5. Quartiles are Hyndman-Fan
   type 7. A prior whale inflates sample σ; it does not drag Q3 and
   IQR the same way. Bessel, MAD, and this fence all fire on the
   planted flush ticket.

10. **Shop-open silent gap.** Walk `TicketOpened` during configured shop
    hours (07:00–19:00 UTC). Shop open and shop close bound the day, so
    a register that never starts — or that dies before close — still
    shows up. Flag any stretch of 45 minutes or longer with no open.
    After-hours silence is ignored. The seeded day stays quiet (longest
    natural hole is just under 45 minutes). A planted lunch-rush hole
    or a dead-register stretch does not.

Each anomaly prints the score, the window or ticket, and the event ids
that justify it. `detect --json` emits the same list as a JSON array.

`stats` folds the same log into a one-screen summary: ticket count,
payment-failure rate, p50/p95 dwell (linear interpolation, Hyndman-Fan
type 7), and how many times each detector fired.
`stats --json` emits the same fold as a JSON object (dwell minutes stay
floats; fail_rate is a 0–1 fraction).

`hours` folds the same log into one line per shop-open hour (07:00–18:00
UTC, plus any hour that actually has events). Empty hours stay in the
table — a quiet mid-morning is a fact, not a missing row. Each line is
tickets opened, payments captured vs failed, revenue from captured
`amount_cents` (integer cents, printed as dollars), and peak concurrent
open tickets in that hour, including cars still sitting from earlier.
`hours --json` emits the same fold as a JSON object (cents stay ints).

`gaps` folds the same log into the shop-hour stretches with no
`TicketOpened`. Shop open and close bound the day; after-hours
silence is ignored. `detect` flags holes of 45 minutes or longer.

`brief` folds the same log into a one-page daily ops view: tickets
opened and closed, payments captured vs failed, revenue from captured
`amount_cents` (integer cents, printed as dollars), leftover still-open
tickets at the end of the log, and the same detector hit counts.
`brief --json` emits the same fold as a JSON object (cents stay ints).

## 2026-08-26

`stats --json`: same day summary as the human `stats` screen, as a
JSON object — shop, day, ticket counts, fail rate, Hyndman-Fan p50/p95
dwell in minutes, and detector hit counts. Pipe it instead of scraping
stdout. Run `python -m event_spine stats --json`.

`hours --json` is the same hourly table as a JSON object — one row
per shop-open hour, plus any hour that actually has events. Revenue
stays integer cents. Run `python -m event_spine hours --json`.

## 2026-08-25

`brief --json`: same daily ops fold as the human brief, as a JSON
object — shop, day, opens/closes, captured vs failed, revenue in
integer cents, leftover open tickets, and detector hit counts.
Pipe it into whatever watches the shop without scraping stdout.
Run `python -m event_spine brief --json`.

## 2026-08-24

Tukey inner fence on ticket totals: score = (x − Q3) / IQR, alarm
at 1.5. Quartiles are Hyndman-Fan type 7. Same $565 flush plant as
the Bessel z-score and the MAD modified z; this one is a rank-based
box instead of a σ estimate, so a prior whale still does not hide
the next one.

Shop-open silent-gap detector: during 07:00–19:00 UTC, flag any stretch
with no `TicketOpened` of 45 minutes or longer. Rebuilt from the
append-only log the same way `hours` is — shop open and close bound the
day, after-hours silence does not count. `gaps` prints the holes;
`detect` includes the check. The seeded day is busy enough to stay
quiet; a planted lunch-rush gap or a dead-register stretch is not.

`brief` rebuilds a one-page daily ops view from the append-only JSONL —
opens and closes, captured vs failed payments, captured revenue,
leftover still-open tickets, and detector hit counts. Run
`python -m event_spine brief`.

## 2026-08-21

Iglewicz-Hoaglin modified z-score on ticket totals (M = 0.6745
(x − median) / MAD, alarm at 3.5). Same $565 flush plant as the
Bessel z-score; this one still fires after a prior whale has
inflated sample σ.

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
event_spine/detect.py     the ten checks
event_spine/stats.py      day summary + percentiles
event_spine/hours.py      hourly fold from the log
event_spine/gaps.py       shop-hour TicketOpened gaps
event_spine/brief.py      daily ops brief from the log
event_spine/report.py     stdout
event_spine/cli.py        simulate | detect | stats | hours | gaps | brief | replay
```

## License

MIT © 2026 Jeff Olfert
