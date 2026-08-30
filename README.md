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
python -m event_spine sku
python -m event_spine sku --json
python -m event_spine bay
python -m event_spine bay --json
python -m event_spine pay
python -m event_spine pay --json
python -m event_spine reason
python -m event_spine reason --json
python -m event_spine dwell
python -m event_spine dwell --json
python -m event_spine vehicle
python -m event_spine vehicle --json
python -m event_spine make
python -m event_spine make --json
python -m event_spine year
python -m event_spine year --json
python -m event_spine model
python -m event_spine model --json
python -m event_spine body
python -m event_spine body --json
python -m event_spine age
python -m event_spine age --json
python -m event_spine origin
python -m event_spine origin --json
python -m event_spine decade
python -m event_spine decade --json
python -m event_spine segment
python -m event_spine segment --json
python -m event_spine size
python -m event_spine size --json
python -m event_spine lines
python -m event_spine lines --json
python -m event_spine tries
python -m event_spine tries --json
python -m event_spine replay --limit 5
python -m event_spine replay --json --limit 5
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
floats; fail_rate is a 0–1 fraction; closed-ticket totals are cents).
The human `stats` screen also prints Hyndman-Fan p50/p95 of closed
ticket totals (integer cents, shown as dollars).

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
`gaps --json` emits the same fold as a JSON object (threshold and
each gap stay structured; no scraping the numbered list).

`brief` folds the same log into a one-page daily ops view: tickets
opened and closed, payments captured vs failed, revenue from captured
`amount_cents` (integer cents, printed as dollars), leftover still-open
tickets at the end of the log, and the same detector hit counts.
`brief --json` emits the same fold as a JSON object (cents stay ints).

`sku` folds the same log into one row per catalog code from
`LineItemAdded`: line count, qty sum, and ext cents (qty × unit_cents).
Sorted highest ext first. `sku --json` emits the same fold as a JSON
object (cents stay ints).

`bay` folds the same log into one row per service bay from the ticket
projection: tickets, closed vs still-open, closed-ticket revenue
(integer cents, printed as dollars), and Hyndman-Fan p50 dwell.
Still-open tickets count toward tickets/open, not revenue or dwell.
Sorted highest revenue first. `bay --json` emits the same fold as a
JSON object (cents stay ints).

`pay` folds the same log into one row per payment method from
`PaymentCaptured` / `PaymentFailed`: captured vs failed counts,
captured cents, and fail rate. Cash stays off the card-terminal
sulk. Sorted highest captured first. `pay --json` emits the same
fold as a JSON object (cents stay ints; fail_rate is a 0–1 fraction).

`reason` folds the same log into one row per `PaymentFailed` reason:
fail count, ask cents, and which tenders saw it. Sorted most fails
first. `reason --json` emits the same fold as a JSON object (cents
stay ints).

`dwell` folds the same log into fixed closed-ticket service bands
(`<5`, `5-15`, `15-60`, `60+` minutes): ticket count, closed-ticket
revenue (integer cents), and Hyndman-Fan p50 dwell inside the band.
Still-open tickets are ignored. Empty bands still print so the
histogram shape stays stable. The seeded three-hour plant lands in
`60+`. `dwell --json` emits the same fold as a JSON object (cents
stay ints).

`vehicle` folds the same log into one row per vehicle from the ticket
projection: tickets, closed vs still-open, closed-ticket revenue
(integer cents, printed as dollars), and Hyndman-Fan p50 dwell.
Still-open tickets count toward tickets/open, not revenue or dwell.
Sorted highest revenue first. `vehicle --json` emits the same fold as a
JSON object (cents stay ints).

`size` folds the same log into fixed closed-ticket total bands
(`<$50`, `$50-100`, `$100-200`, `$200+`): ticket count, closed-ticket
revenue (integer cents), and Hyndman-Fan p50 total inside the band.
Still-open tickets are ignored. Empty bands still print so the
histogram shape stays stable. The seeded $565 flush plant lands in
`$200+`. `size --json` emits the same fold as a JSON object (cents
stay ints).

`lines` folds the same log into fixed closed-ticket line-count bands
(`1`, `2`, `3`, `4+`): ticket count, closed-ticket revenue (integer
cents), and Hyndman-Fan p50 total inside the band. Still-open tickets
are ignored. Empty bands still print so the histogram shape stays
stable. On the seeded day most oil changes sit in `3` or `4+`; the
flush ticket lands in `4+`. `lines --json` emits the same fold as a
JSON object (cents stay ints).

`tries` folds the same log into fixed closed-ticket payment-attempt
bands (`1`, `2`, `3+`): ticket count, closed-ticket revenue (integer
cents), and Hyndman-Fan p50 total inside the band. Count is
captures + fails on the ticket projection. Still-open tickets are
ignored. Empty bands still print so the histogram shape stays stable.
On the seeded day the 4pm card-terminal sulk lands six tickets in
`3+`. `tries --json` emits the same fold as a JSON object (cents stay
ints).

`make` folds the same log into one row per vehicle manufacturer parsed
from `TicketOpened` (skip a leading four-digit year, keep the next
token). Tickets, closed vs still-open, closed-ticket revenue (integer
cents), and Hyndman-Fan p50 dwell. Still-open tickets count toward
tickets/open, not revenue or dwell. Sorted highest revenue first.
`make --json` emits the same fold as a JSON object (cents stay ints).

`year` folds the same log into one row per model year parsed
from `TicketOpened` (a leading four-digit year, or empty if none).
Tickets, closed vs still-open, closed-ticket revenue (integer
cents), and Hyndman-Fan p50 dwell. Still-open tickets count toward
tickets/open, not revenue or dwell. Sorted highest revenue first.
`year --json` emits the same fold as a JSON object (cents stay ints).

`model` folds the same log into one row per vehicle model parsed
from `TicketOpened` (skip a leading four-digit year and the make
token, join the rest). Tickets, closed vs still-open, closed-ticket
revenue (integer cents), and Hyndman-Fan p50 dwell. Still-open
tickets count toward tickets/open, not revenue or dwell. Sorted
highest revenue first. `model --json` emits the same fold as a JSON
object (cents stay ints).

`body` folds the same log into one row per vehicle body classified
from `TicketOpened` (parse the model, then truck / SUV / car).
Tickets, closed vs still-open, closed-ticket revenue (integer
cents), and Hyndman-Fan p50 dwell. Still-open tickets count toward
tickets/open, not revenue or dwell. Sorted highest revenue first.
`body --json` emits the same fold as a JSON object (cents stay ints).

`age` folds the same log into one row per vehicle age band
(`0-4`, `5-9`, `10-14`, `15-19`, `20+`) from `TicketOpened` year
versus the shop day's year. Tickets, closed vs still-open,
closed-ticket revenue (integer cents), and Hyndman-Fan p50 dwell.
Still-open tickets count toward tickets/open, not revenue or dwell.
Sorted highest revenue first, then newest band. `age --json` emits
the same fold as a JSON object (cents stay ints).

`origin` folds the same log into one row per vehicle origin
(Japan, US, Korea, Germany) classified from `TicketOpened` make.
Tickets, closed vs still-open, closed-ticket revenue (integer
cents), and Hyndman-Fan p50 dwell. Still-open tickets count toward
tickets/open, not revenue or dwell. Sorted highest revenue first.
`origin --json` emits the same fold as a JSON object (cents stay ints).

`decade` folds the same log into one row per model-year decade
(2018 → 2010s, 2022 → 2020s) classified from `TicketOpened` year.
Tickets, closed vs still-open, closed-ticket revenue (integer
cents), and Hyndman-Fan type-7 dwell p50. Sorted highest revenue
first, then newest decade. `decade --json` emits the same fold as
a JSON object (cents stay ints).

`segment` folds the same log into one row per vehicle market
segment (luxury, truck, suv, car) classified from `TicketOpened`
make/model. Pickup names win even on a luxury badge; luxury makes
beat SUV names; everything else with a parseable plate is car.
Tickets, closed vs still-open, closed-ticket revenue (integer
cents), and Hyndman-Fan p50 dwell. Still-open tickets count toward
tickets/open, not revenue or dwell. Sorted highest revenue first.
`segment --json` emits the same fold as a JSON object (cents stay
ints).

## 2026-08-30

`segment` rebuilds one row per market segment from the same plate
(`2018 Honda Civic` → car, `2021 Toyota RAV4` → suv,
`2015 Ford F-150` → truck, `2017 BMW 328i` → luxury). A pickup
beats a luxury badge (`2024 Tesla Cybertruck` → truck).
Closed-ticket line totals become revenue; leftover open tickets
stay in the open column. Human screen prints dollars; `--json`
keeps cents as numbers. Run
`python -m event_spine segment` or `python -m event_spine segment --json`.

## 2026-08-29

`year` rebuilds one row per model year from `TicketOpened.vehicle`
on the ticket projection (`2018 Honda Civic` → 2018). Closed-ticket
line totals become revenue; leftover open tickets stay in the open
column. 2018, 2019, and 2022 show up next to the rest of the
seeded lot. Human screen prints dollars; `--json` keeps cents as
numbers. Run
`python -m event_spine year` or `python -m event_spine year --json`.

`body` rebuilds one row per body class from the same plate
(`2018 Honda Civic` → car, `2021 Toyota RAV4` → SUV,
`2015 Ford F-150` → truck). Closed-ticket line totals become
revenue; leftover open tickets stay in the open column. Human
screen prints dollars; `--json` keeps cents as numbers. Run
`python -m event_spine body` or `python -m event_spine body --json`.

`age` rebuilds one row per years-old band from the same plate
(`2022 Ford Escape` → 0-4, `2018 Honda Civic` → 5-9,
`2015 Ford F-150` → 10-14, `2011 Toyota Camry` → 15-19).
Closed-ticket line totals become revenue; leftover open tickets
stay in the open column. Human screen prints dollars; `--json`
keeps cents as numbers. Run
`python -m event_spine age` or `python -m event_spine age --json`.

`origin` rebuilds one row per manufacturer origin from the same
plate (`2018 Honda Civic` → Japan, `2015 Ford F-150` → US,
`2020 Hyundai Tucson` → Korea, `2017 BMW 328i` → Germany).
Closed-ticket line totals become revenue; leftover open tickets
stay in the open column. Human screen prints dollars; `--json`
keeps cents as numbers. Run
`python -m event_spine origin` or `python -m event_spine origin --json`.

`decade` rebuilds one row per model-year decade from the same
plate (`2018 Honda Civic` → 2010s, `2021 Toyota RAV4` → 2020s).
Closed-ticket line totals become revenue; leftover open tickets
stay in the open column. Human screen prints dollars; `--json`
keeps cents as numbers. Run
`python -m event_spine decade` or `python -m event_spine decade --json`.

## 2026-08-28

`vehicle` rebuilds one row per car from `TicketOpened` → ticket
projection. Closed-ticket line totals become revenue; leftover open
tickets stay in the open column. On the seeded day the Silverado and Escape
lead the lot by closed revenue. Human screen prints dollars; `--json` keeps cents as
numbers. Run
`python -m event_spine vehicle` or `python -m event_spine vehicle --json`.

`size` rebuilds a four-band histogram of closed ticket totals from
line-item sums on the ticket projection. Most oil changes sit in
`$50-100`; the planted $565 flush sits alone in `$200+`. Human screen
prints dollars; `--json` keeps cents as numbers. Run
`python -m event_spine size` or `python -m event_spine size --json`.

`lines` rebuilds a four-band histogram of closed-ticket basket depth
from the ticket projection. Single-SKU tickets stay rare on the seeded
day; the flush and multi-add tickets land in `4+`. Human screen prints
dollars; `--json` keeps cents as numbers. Run
`python -m event_spine lines` or `python -m event_spine lines --json`.

`tries` rebuilds a three-band histogram of closed-ticket payment
attempts from the ticket projection. First-try captures fill `1`; the
planted 4pm terminal sulk puts six tickets in `3+`. Human screen prints
dollars; `--json` keeps cents as numbers. Run
`python -m event_spine tries` or `python -m event_spine tries --json`.

`make` rebuilds one row per manufacturer from `TicketOpened.vehicle`
on the ticket projection (`2018 Honda Civic` → Honda). Closed-ticket
line totals become revenue; leftover open tickets stay in the open
column. Honda, Toyota, and Ford show up next to the rest of the
seeded lot. Human screen prints dollars; `--json` keeps cents as
numbers. Run
`python -m event_spine make` or `python -m event_spine make --json`.

## 2026-08-27

`sku` rebuilds the day's menu mix from `LineItemAdded` alone — no
ticket projection required. Whale SKUs (`TRN-FLUSH`, `DIFF-FLUID`,
`BRK-FLUSH`) show up next to the oil-change baseline. Human screen
prints dollars; `--json` keeps cents as numbers. Run
`python -m event_spine sku` or `python -m event_spine sku --json`.

`bay` rebuilds one row per service bay from `TicketOpened` → ticket
projection. Closed-ticket line totals become revenue; leftover open
tickets stay in the open column. Human screen prints dollars;
`--json` keeps cents as numbers. Run
`python -m event_spine bay` or `python -m event_spine bay --json`.

`pay` rebuilds one row per tender from the payment events. Card
carries the planted 16:03 declines; cash does not. Captured cents
are the same revenue hours uses. Human screen prints dollars;
`--json` keeps cents as numbers. Run
`python -m event_spine pay` or `python -m event_spine pay --json`.

`reason` rebuilds the fail mix from `PaymentFailed` alone — on the
seeded day that is network on card, ask dollars matching the
declined tickets. Run
`python -m event_spine reason` or `python -m event_spine reason --json`.

`dwell` rebuilds a four-band histogram from open/close facts on the
ticket projection. Most oil changes sit under five minutes; the
planted 09:42 long bay sits alone in `60+`. Human screen prints
dollars; `--json` keeps cents as numbers. Run
`python -m event_spine dwell` or `python -m event_spine dwell --json`.

## 2026-08-26

`stats --json`: same day summary as the human `stats` screen, as a
JSON object — shop, day, ticket counts, fail rate, Hyndman-Fan p50/p95
dwell in minutes, and detector hit counts. Pipe it instead of scraping
stdout. Run `python -m event_spine stats --json`.

`hours --json` is the same hourly table as a JSON object — one row
per shop-open hour, plus any hour that actually has events. Revenue
stays integer cents. Run `python -m event_spine hours --json`.

`gaps --json` is the silent-gap list as a JSON object — shop, day,
45-minute threshold, shop-open window, and each hole with the same
fields as `detect --json`. Run `python -m event_spine gaps --json`.

`replay --json` is the ticket projection as a JSON object — shop,
day, and each ticket with line items and payments (cents stay ints).
`--limit` still caps the list. Run `python -m event_spine replay --json`.

`stats` now includes closed-ticket total p50/p95 (same Hyndman-Fan
type 7 as dwell). Cents stay numbers in `--json`; the human screen
prints them as dollars.

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
event_spine/sku.py        SKU fold from LineItemAdded
event_spine/bay.py        per-bay tickets, revenue, dwell
event_spine/dwell.py      closed-ticket dwell bands
event_spine/vehicle.py    per-vehicle tickets, revenue, dwell
event_spine/size.py       closed-ticket total bands
event_spine/lines.py      closed-ticket line-count bands
event_spine/tries.py      closed-ticket payment-attempt bands
event_spine/make.py       per-make tickets, revenue, dwell
event_spine/year.py       per-year tickets, revenue, dwell
event_spine/model.py      per-model tickets, revenue, dwell
event_spine/body.py       per-body tickets, revenue, dwell
event_spine/age.py        per-age-band tickets, revenue, dwell
event_spine/origin.py     per-origin tickets, revenue, dwell
event_spine/decade.py     per-decade tickets, revenue, dwell
event_spine/segment.py    per-segment tickets, revenue, dwell
event_spine/pay.py        per-method captured vs failed
event_spine/reason.py     PaymentFailed reason fold
event_spine/report.py     stdout
event_spine/cli.py        simulate | detect | stats | hours | gaps | brief | sku | bay | dwell | size | lines | tries | vehicle | make | year | model | body | age | origin | decade | segment | pay | reason | replay
```

## License

MIT © 2026 Jeff Olfert
