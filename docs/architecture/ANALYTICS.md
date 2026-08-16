# Analytics Architecture & Data Integrity

**Status:** Current as of PH3.8 (2026-08-16)
**Scope:** Every number this product displays to a user or an administrator —
where it comes from, what time window it covers, which timezone that window is
anchored in, and whether a reader may act on it.

> **How this document relates to the others.**
> [`OBSERVABILITY.md`](OBSERVABILITY.md) covers metrics *about the system* —
> request rates, error counts, latency histograms. **This** document covers
> metrics *about the business and the user's money* — P&L, win rate, portfolio
> value, revenue, engagement. They are different problems: an observability
> metric that is slightly wrong costs an operator ten minutes; a P&L figure that
> is slightly wrong costs a trader a decision.

---

## 1. The question this exists to answer

There is exactly one:

> **For any number on any screen: may I act on it?**

Before PH3.8 that question was unanswerable from the payload. Three fields from
one real response:

```json
{ "mrr": 12475, "retention_rate": 78.5, "revenue_today": 0 }
```

`mrr` is a real user count multiplied by a hardcoded price. `retention_rate` is
a literal typed into `server.py`. `revenue_today` is `0` because the `payments`
collection is empty — indistinguishable, in that payload and on the dashboard
that renders it, from "we made no money today". Three numbers, three completely
different epistemic statuses, identical presentation.

**Zero is the dangerous value.** An unavailable metric rendered as `0` is not a
missing number; it is a *wrong* number that looks authoritative and formats
beautifully. Everything below follows from taking that seriously.

---

## 2. The four classifications

Every metric in the product carries exactly one:

| Class | Meaning | Rendered as |
|---|---|---|
| **REAL** | Read directly from persisted production records | The value |
| **DERIVED** | Computed deterministically from persisted production records | The value |
| **MOCK** | Fabricated — hardcoded, formula-invented, or randomised | The value, **visibly marked "Simulated"** |
| **UNAVAILABLE** | A legitimate metric whose required source data does not exist | An explicit empty state, **never a zero** |

**An endpoint existing is not evidence a metric is real.** Each classification
in this document was reached by tracing the complete path — collection →
repository/service → route → frontend component → the pixels a user sees. Where
that trace ends at a literal, a formula over a proxy, or a collection nothing
writes to, the classification says so.

### 2.1 Where the classification lives

`backend/analytics/registry.py`. **As code, not as a table in this file.**

An inventory of "which of our numbers are real" is worth exactly as much as its
accuracy on the day somebody reads it, and a markdown table drifts silently
forever after it is written. The registry is imported by
`backend/tests/test_analytics.py`, which asserts that every endpoint it names
still exists on the live route table, that every entry carries a valid
classification, and that every MOCK entry names the production source that
would replace it. A mock removed without updating its entry fails the suite; an
analytics endpoint added without an entry fails the suite.

The tables in §10 of this document are a **rendering** of that registry, not a
second source of truth. When they disagree, the registry is right.

### 2.2 Current totals

| Class | Count |
|---|---|
| REAL | 4 |
| DERIVED | 26 |
| MOCK | 17 |
| UNAVAILABLE | 5 |

Seventeen fabricated metrics remain in production. **PH3.8 did not remove
them** — that is PH3.9's sprint, and pulling a chart out of a dashboard without
its replacement is not an improvement. What PH3.8 removed is the *impression*
that they are measured. Every one now declares itself in its API response and
renders behind a visible marker.

---

## 3. Source-of-truth model

| Domain | Authority | Notes |
|---|---|---|
| Real-money trades | `db.trades` where `is_paper != true` | The platform's record of broker fills. The broker is the upstream authority. |
| Paper trades | `db.trades` where `is_paper == true` | Virtual capital. Same collection, **never the same statistic.** |
| Broker holdings | `db.holdings` | Synced positions. Primary over manual trades on symbol collision. |
| Portfolio equity history | `db.portfolio_snapshots` | One row per user per IST day. Built forward from real marks; **never back-filled.** |
| Live prices | Market Gateway → `real_quotes_map` | Never a provider call from business logic (`MARKET_DATA_ARCHITECTURE.md`). |
| Users & plans | `db.users` | `role` is the plan field. **Granted by admins, not by payment** — see §7. |
| Revenue | *(nothing)* | `db.payments` **has no writer anywhere in the codebase.** |
| Sessions / activity | `db.sessions` (`last_used_at`) | Real activity data, **not currently read by any analytics.** |
| AI & provider usage | `observability.metrics` | Real counters and histograms, **not currently read by the admin portal.** |

Two rules, both violated somewhere before PH3.8:

1. **Never compute a business metric from frontend state when a backend source
   of truth exists.**
2. **Never trust a client-submitted financial value for an authoritative
   metric.** Trade P&L is computed server-side from stored entry/exit prices.

### 3.1 Trade scoping is centralised

The same three scoping decisions were being re-made, inconsistently, at every
call site touching `db.trades`:

- **Is a paper trade included?** `build_risk_summary` excluded them.
  `build_holdings` excluded them. `build_intelligence`'s realised P&L — *inside
  the same function as `build_holdings`* — included them, as did the trade
  journal and `GET /api/trades/pnl`. One collection, two meanings of "my
  trades", decided independently in six places.
- **What counts as closed?** Some sites test `status != "OPEN"`, others
  `status == "CLOSED"`. The lifecycle writes `CLOSED`, `TARGET_HIT` **and**
  `SL_HIT`, so the second form silently drops every trade that exited at a
  target or a stop — which is most of them.
- **Which day is "today"?** Every site wrote its own UTC-day prefix match.

`backend/analytics/queries.py` now owns all three. It builds **filters only** —
no arithmetic — so `services.portfolio_engine` and `services.trading_engine`
remain the single source of truth for the math. The scoping decisions are what
drifted; the scoping decisions are what got centralised.

Two subtleties worth keeping:

- `{"is_paper": {"$ne": True}}` and **not** `{"is_paper": False}`. Trades
  written before paper trading existed have no `is_paper` field, and an
  equality match would drop every one of them.
- `closed()` uses `status != "OPEN"`, the deliberately *wider* form. A trade in
  a status nobody anticipated is still not open, and a realised-P&L total that
  silently omits it is worse than one that includes it. `analytics.quality`
  reports unknown statuses so the gap stays visible.

---

## 4. The analytics contract

`backend/analytics/contract.py`. Every metric carries name, value, unit, the
resolved window, provenance, status and computation time:

```json
{
  "name": "retention_rate",
  "value": 78.5,
  "unit": "percent",
  "status": "mock",
  "provenance": "mock",
  "note": "A constant in the source. It does not move when users leave.",
  "calculated_at": "2026-08-16T10:04:11.902Z",
  "period": { "key": "today", "start": "...", "end": "...", "timezone": "Asia/Kolkata" }
}
```

Four invariants, **enforced at construction** so a malformed metric fails in the
test that builds it rather than in production when somebody reads it:

1. `status` in `{unavailable, empty}` ⟹ `value is None`. An uncomputable metric
   has no value, and cannot be coerced to `0` on the far side of an HTTP
   boundary because there is nothing there to coerce.
2. `provenance == mock` ⟹ `status == mock`. Setting one without the other is
   how a fabricated number reaches a dashboard that checks only the other.
3. `status` in `{unavailable, mock}` ⟹ a non-empty `note`. A metric a user
   cannot trust needs a reason they can read.
4. `provenance`, `status` and `unit` come from closed vocabularies.

`EMPTY` is deliberately distinct from `UNAVAILABLE`: "you have closed no trades
yet" is a true fact about this user; "we cannot compute revenue because no
payment records exist anywhere" is a gap in the platform. Conflating them tells
a new user the product is broken.

### 4.1 The contract is additive

PH3.8 is an audit sprint. Every existing flat key is preserved and an
`analytics` block is added *beside* it, along with a `mock_metrics` array for
consumers that only want the flag. Nothing regressed: all 2,303 pre-existing
backend tests and all 364 pre-existing frontend tests pass unchanged. PH3.9 may
retire the flat keys once the mocks behind them are gone.

---

## 5. Time windows and the timezone strategy

### 5.1 The strategy

> **Storage is UTC. Boundaries are IST. Nothing is ever computed in
> server-local or browser-local time.**

Timestamps are persisted exactly as before — timezone-aware UTC ISO-8601
strings. No migration was needed and none was performed. Every *window* is
resolved against `Asia/Kolkata`, the exchange timezone, then converted back to
UTC for querying.

**Why IST.** The product is an NSE trading platform. Its market, its users, its
currency and its scheduler (`AsyncIOScheduler(timezone="Asia/Kolkata")`) are all
IST. A UTC day rolls at **05:30 IST**, so "today" silently meant "since 05:30
this morning" — continuous intraday metrics were unaffected, because the
09:15–15:30 IST session sits inside a single UTC date, but every boundary near
midnight was attributed to the wrong day and nothing in the codebase said which
timezone any window was in.

`analytics.periods.IST` is a fixed `+05:30` offset, not a `ZoneInfo` lookup:
India has observed no DST since 1945 and has one timezone, so the offset is a
constant — and a fixed offset cannot fail at runtime on a host without tzdata,
which `ZoneInfo("Asia/Kolkata")` can and does inside slim containers.

### 5.2 Window semantics

Every window is **half-open**, `[start, end)`. Half-open is what makes adjacent
windows partition time exactly once: an event at midnight belongs to the later
day and to no other, so "today" + "yesterday" can never double-count and can
never drop an instant.

| Key | Definition (all IST) |
|---|---|
| `today` | 00:00 today → 00:00 tomorrow |
| `yesterday` | 00:00 yesterday → 00:00 today |
| `7d` / `30d` / `90d` | N **whole days ending with today** → 00:00 tomorrow |
| `mtd` | 00:00 on the 1st of this month → 00:00 tomorrow |
| `prev_month` | 00:00 on the 1st of last month → 00:00 on the 1st of this month |
| `ytd` | 00:00 on 1 January → 00:00 tomorrow |
| `all` | unbounded → 00:00 tomorrow |

`7d` is *seven whole days*, not *the last 168 hours*. A trader comparing a
7-day figure across two page loads an hour apart expects the same number; a
rolling-instant window silently changes it.

`all` carries `start = None` — unbounded is represented honestly rather than as
a sentinel date, so a caller that must reject unbounded scans can detect one.

An unknown period key **raises**. Falling back to "all time" turns a typo in a
query parameter into a metric that silently covers the wrong span.

### 5.3 Range comparisons replaced prefix matching

`exit_time.startswith("2026-08-16")` can only ever express a UTC day, because
the prefix it matches *is* the stored UTC date. An IST day is not a prefix of
anything. A range comparison is the only form that can express it — and it is
also the faster form: `{"$gte": ..., "$lt": ...}` is served by a B-tree index,
while `$regex` on an unindexed string field is a collection scan. The
lexicographic ordering of same-offset ISO-8601 strings is identical to
chronological ordering, so the comparison is correct on the strings already
stored.

### 5.4 Market-session semantics

For trading analytics the unit is the **session**: 09:15–15:30 IST, Monday to
Friday. `analytics.periods.session_date()` returns the IST calendar date and
whether the instant fell inside a session.

**Exchange holidays are not modelled.** There is no holiday calendar and the
module does not pretend otherwise — `is_trading_day` is a weekday check. A
metric that depends on real trading days must say so rather than silently
treating Diwali as a session.

---

## 6. Financial metric semantics

### 6.1 Everything is GROSS

**No brokerage, STT, exchange transaction charge, GST, SEBI turnover fee or
stamp duty is recorded anywhere in this product.** Every P&L figure displayed —
realised, unrealised, per-trade, portfolio-level, paper — is gross of charges.
Responses carry `"basis": "gross"` to say so.

This is why **profit factor, expectancy and a trade-level Sharpe ratio are
classified UNAVAILABLE rather than implemented.** They are computable in
principle from closed trades. On Indian intraday equity, charges routinely
exceed the edge on a small trade, so a profit factor computed gross is
systematically optimistic — and a trader would act on it. Making the number
appear because the arithmetic is possible is precisely what this sprint refused
to do.

Real charges need per-fill data from the broker contract note. Until that
exists, the honest figure is the gross one, labelled.

### 6.2 Sign conventions

One convention, applied everywhere, side-aware:

```
per_share_pnl = (entry − exit) if SELL else (exit − entry)
pnl           = per_share_pnl × quantity
pnl_percent   = pnl / (entry × original_quantity) × 100
```

A short profits when the price falls. `analytics.quality` re-derives stored P&L
from entry/exit/quantity and flags a disagreement over ₹1, which catches a sign
error on a short and a P&L written against the wrong quantity.

### 6.3 Outcomes are three, not two

**Win, loss and breakeven.** A trade closing at exactly ₹0 is neither won nor
lost. It was counted as a loss before PH3.8 (`if pnl > 0 ... else loss`), which
mattered more than it sounds: `reset_paper_capital` force-closes open positions
with `pnl: 0`, so resetting a paper account manufactured a run of recorded
losses and pushed the displayed win rate down for as long as those rows existed.

`win_rate` keeps its denominator as the full closed-trade count, so the three
outcomes sum to 100%.

Those reset rows now also carry `close_reason: "capital_reset"` — they are not
closed at breakeven, they are *abandoned* at a fabricated ₹0, and without a
marker they are indistinguishable from real breakeven exits.

### 6.4 Partial exits

A trade that books half its size at target 1 writes `realized_pnl` but not
`pnl` — `pnl` is only written at full close. Every realised-P&L metric keys off
`pnl`, so booked profit was invisible to all of them, **including the one that
enforces `max_daily_loss`.** That is a risk control, not a display concern: the
loss budget could not see money already realised today.

`apply_partial_exit` now appends to a dated `bookings` timeline (`at`,
`quantity`, `price`, `pnl`, `reason`), and `build_risk_summary` includes
bookings dated inside the window **for still-open trades only** — once a trade
closes, its `pnl` is the total of every booking and counting both would double
it.

### 6.5 Portfolio return is not flow-adjusted

`portfolio.pct_return` is a change in portfolio **value**, and value rises both
when holdings gain and when the user puts more money in. Depositing ₹1L into a
₹1L portfolio reports **+100%** — measured, and completely wrong as a
performance figure.

A correct time-weighted return needs a dated ledger of contributions and
withdrawals, which this platform does not record. **PH3.8 did not invent one.**
What it did:

- `invested` is stored on every snapshot, so a change in cost basis across the
  window is a reliable **detector** of a flow, even though it cannot size the
  return correction.
- The payload now carries `flow_adjusted: false`, `flows_detected`,
  `invested_change` and a human-readable `caveat` when a flow is present.
- A day whose cost basis moved is **excluded from best-day / worst-day**. A
  deposit is not a "best day" and must not be reported as one.

`portfolio.time_weighted_return` is registered UNAVAILABLE with its required
source. Inventing a TWR from the data that exists would be the single most
damaging fabrication in the product, because a trader would compare it to a
benchmark.

### 6.6 Paper trading

Virtual capital is fictional **by design**, which is a different thing from a
fabricated metric — the arithmetic is real and runs over real quotes. Paper
figures are DERIVED, and rigorously isolated from real-money statistics **in
both directions**.

`total_pnl_pct` divides by the fixed ₹1,00,000 starting capital rather than the
current balance, by design: it is a return on the account's inception, the only
denominator that stays stable as positions open and close.

An open position whose live quote cannot be fetched contributes exactly ₹0 of
unrealised P&L — indistinguishable from a position that has not moved. The count
is now returned as `marks_unavailable` with a `complete` flag so a partial
figure is not presented as a complete one.

### 6.7 Equity curve

Built forward from real end-of-day marks, one per user per IST day, upserted by
the 16:05 IST job. **Never back-filled with synthetic history.** Returns
`available: false` with a reason below two snapshots — not a flat line at zero.

Range keys are **calendar days**. They were previously applied as `snaps[-days:]`
— a slice of the *list*, so the unit was "snapshots", not days. Snapshots are
written only when the job runs and only for users holding something, so the two
units diverge on the first gap: a fixture with one snapshot per month returned
**thirty months** of history for `range=1M`, labelled "1M".

---

## 7. Revenue: the structural gap

Every revenue metric in the admin portal is fabricated, and the reason is not a
coding slip:

> **`db.payments` has no writer anywhere in the codebase. The platform has no
> payment integration.** The collection is read by three admin endpoints and
> indexed at startup. Nothing has ever written to it.

Consequently:

| Metric | What it actually is |
|---|---|
| `mrr` | role counts × a hardcoded ₹499/₹999 |
| `arr` | the above × 12 |
| `revenue_today` (dashboard) | **count** of all payment documents × ₹499 — not a sum, not date-filtered |
| `revenue_today` / `revenue_week` (payments) | literal `0` |
| `revenue_month` / `revenue_year` | `mrr` / `arr` |
| `pending_payments` / `refunds` / `failed_payments` | literal `0` |
| 30-day revenue series | `2500 + i×150 + (500 if i % 7 == 0)` — no database access at all |

Two consequences worth stating plainly:

- **`mrr` counts comped accounts as paying.** `role` is assigned by an admin
  through `POST /api/admin/users/{id}/grant-plan` with no payment involved, so
  every internal account, every beta tester and every comped user is revenue.
- **`revenue_today` reads ₹0 only because the collection is empty.** The first
  record to land reports ₹499 of "today's revenue" whatever it was actually for.
- **`refunds: 0` is contradicted by the product itself.** PH3.5 found that
  `POST /api/admin/payments/{id}/refund` is a stub returning success while
  writing a `payment.refunded` audit record for a refund that never happened
  (D-4, owned by PH3.9). Refunds read as zero while the audit log says otherwise.

Plan prices are now the named constant `_ASSUMED_PLAN_PRICE_INR` rather than
magic numbers inline — not because that makes the estimate correct, but so the
assumption is greppable when PH3.9 replaces it.

---

## 8. Data quality

`backend/analytics/quality.py`. **It reports. It never writes, never repairs,
never silently excludes.**

- Silently mutating production records so a dashboard adds up is how a data
  problem becomes an unrecoverable data problem. The bad row is the evidence.
- Silently *excluding* bad rows is nearly as bad — the metric becomes correct
  and quietly incomplete, and nobody learns the collection is damaged.

Where a metric must exclude a record to stay defensible (a trade with no usable
timestamp cannot be attributed to a day), the exclusion happens in the metric
and is **counted**, so it can be surfaced rather than lost.

Each check corresponds to a state PH3.8 found *reachable in the current code*,
not to a generic hygiene checklist:

| Code | Why it matters |
|---|---|
| `pnl_without_exit_time` | The exact shape that crashed the end-of-day job (F-2) |
| `unknown_status` | Metrics testing `!= OPEN` and `== CLOSED` disagree about the trade |
| `closed_without_exit_time` | Invisible to every period-scoped metric; cannot be dated |
| `open_with_pnl` | Realised P&L double-counts a live position |
| `quantity_open_exceeds_quantity` | Partial-exit bookkeeping has drifted |
| `closed_with_open_quantity` | Exposure counts a position that no longer exists |
| `pnl_mismatch` | Sign error on a short, or P&L against the wrong quantity |
| `paper_trade_with_broker` | Virtual trade may be counted as real order flow |
| `duplicate_snapshot_date` | One day double-counted on the equity curve |
| `snapshot_pnl_mismatch` | Stored `pnl` disagrees with `current_value − invested` |
| `negative_amount`, `missing_status`, `missing_currency`, `invalid_created_at` | Payments cannot be summed or placed in a period |

Reports are bounded to 100 issues with a `truncated` flag — a scan of a damaged
collection must not itself become the outage.

**On this installation the payment scan reports zero over zero records**, which
is the honest result and the point: every revenue metric in the admin portal is
computed without this data.

---

## 9. Performance

### 9.1 Indexes added

| Index | Query it serves | What it replaced |
|---|---|---|
| `portfolio_snapshots {user_id, date}` **unique** | Performance tab filter + range + sort; the nightly upsert | **No index of any kind since Sprint 8.** Every Portfolio page load scanned every user's history, and the 16:05 job scanned the whole collection once per user — O(users²) work in an unattended job. `unique` also makes one-snapshot-per-user-per-day the database's rule rather than the upsert's assumption. |
| `users {created_at}` | Admin signup counts | A `$regex` prefix match on an unindexed string — a full scan of the users collection on every admin page load, growing with total signups forever |
| `chat_messages {created_at}` | "AI requests today" | Same. Neither existing compound index can serve it: they lead with `user_id`/`session_id`, and this query constrains neither |

Every one is pinned in `tests/test_perf_regression.py::HOT_QUERIES`, so removing
an index or changing a query shape fails the suite.

### 9.2 Unbounded work removed

| Path | Before | After |
|---|---|---|
| `eod_report_job` | `db.trades.find({}).to_list(1000)` — the **whole platform's** trades, natural order | Windowed query, per-user grouping |
| `GET /api/trades/pnl` | `find(...).to_list(500)`, summed in Python | `$group` + counts; **no document crosses the wire** |
| `portfolio_summary` / `build_intelligence` realised P&L | `to_list(500)`, no sort | `$group`, uncapped |
| `journal/stats`, `setup-stats` | `to_list(500)`, no sort | Uncapped, correct |
| `get_performance` | `to_list(1000)` then sort in Python | Sort and range in the database |
| Admin plan breakdown | `async for` over every user document, on two endpoints | `$group` |

The `to_list(500)` caps were not a performance guard — they applied **no sort**,
so the "all-time" figure for an active trader was the sum of an arbitrary 500
rows chosen by Mongo's natural order. Removing them is a correctness fix that
happens to also remove a wire cost.

### 9.3 Measured

Query counts per request, at 2,000 closed trades and 1,200 snapshots for one
user, against the in-memory test double:

| Endpoint | Queries | Payload |
|---|---|---|
| `GET /api/trades/pnl` | 11 (7 analytics, issued concurrently) | 0.3 KiB |
| `GET /api/journal/stats` | 7 | 0.7 KiB |
| `GET /api/journal/setup-stats` | 5 | 0.2 KiB |
| `GET /api/trades/risk/summary` | 5 | 0.2 KiB |
| `GET /api/portfolio/summary` | 7 | — |
| `GET /api/portfolio/performance` | 5 | ∝ points |

**Flat in the number of trades** — no N+1. Absolute latencies are not reported
because the double is a synchronous in-memory list with no round-trip cost: it
cannot show the benefit of the `asyncio.gather` on `/api/trades/pnl`, and its
per-query cost is O(collection) where Mongo's is O(log n). Real numbers need
`scripts/perf_db_benchmark.py` against a real MongoDB, which is PH2.12 staging
work.

### 9.4 Paths that remain expensive, and why

| Path | Cost | Why it is accepted |
|---|---|---|
| `GET /api/journal/stats`, `setup-stats` | O(user's closed trades) documents | Needs best/worst/average over the set, which `$group` in this codebase's FakeDB-compatible subset cannot express. Bounded by the **user's own** data, not by total signups — a different growth shape from the pre-PH3.4 scans. Revisit past ~10k trades/user. |
| `GET /api/portfolio/performance?range=ALL` | Payload ∝ snapshot count | ~1,000 points ≈ 60 KiB after four years. Bounded ranges are cheap; `ALL` should gain downsampling before it matters. |
| `GET /api/paper/pnl`, `/api/paper/trades` | One market-data call **per open position, sequentially** | A genuine N+1 over the network. Not fixed here: the fan-out helper (`real_quotes_map`) is the right tool and swapping it in touches the paper-trading write path, which is out of an analytics sprint's scope. **Carried as PH3.9 debt.** |

### 9.5 Caching policy

**No analytics caching was added, and Redis was deliberately not used.**

Redis exists (PH2.7) and caching these endpoints would be easy. It would also
have been wrong at this point: correctness semantics were the thing under
repair, and caching a metric whose window semantics had just changed would
freeze the old answer behind a TTL and make the next defect much harder to see.
Cache an analytics figure only when its invalidation is understood and stale
data is acceptable for that specific number — which, for a live P&L, it is not.

---

## 10. The inventory

Rendered from `backend/analytics/registry.py`. **The registry is authoritative.**

### 10.1 REAL

| Metric | Endpoint | Source |
|---|---|---|
| Total users | `GET /api/admin/dashboard` | `db.users` |
| Pro / Elite / Lifetime counts | `GET /api/admin/dashboard`, `/payments/stats` | `db.users.role` |
| Open positions | `GET /api/trades/pnl` | `db.trades.status` |
| CPU / RAM / disk | `GET /api/admin/system/health` | `psutil` (single process only) |

### 10.2 DERIVED (selected)

| Metric | Endpoint | Notes |
|---|---|---|
| Total / today P&L, win rate | `GET /api/trades/pnl` | Live-only, gross, IST window |
| Realised P&L today, open risk, open exposure | `GET /api/trades/risk/summary` | Feeds the daily-loss halt. Exposure is at **cost**, not market |
| Win rate, P&L, best/worst (period + all-time) | `GET /api/journal/stats` | Live-only; paper reported separately |
| Win rate by setup | `GET /api/journal/setup-stats` | Untagged trades excluded, not bucketed |
| Paper realised / unrealised / return % | `GET /api/paper/pnl` | Virtual capital, isolated |
| Portfolio value, unrealised, realised | `GET /api/portfolio/summary` | Broker-primary merge |
| Allocation, HHI, risk score, movers, dividends | `GET /api/portfolio/intelligence` | Risk score is a declared **heuristic**, not a VaR |
| Equity curve, return over period | `GET /api/portfolio/performance` | Not flow-adjusted — §6.5 |
| Backtest metrics (yfinance path only) | `POST /api/backtest` | The fallback path is MOCK |
| Conversion rate | `GET /api/admin/analytics/users` | Conversion to a **granted role**, not to a paid plan |
| Top AI users | `GET /api/admin/ai/usage` | The count is real; the cost beside it is not |

### 10.3 MOCK — the PH3.9 removal inventory

See §11.

### 10.4 UNAVAILABLE

| Metric | Blocked on | Recommendation |
|---|---|---|
| `portfolio.time_weighted_return` | A dated external cash-flow ledger | Show UNAVAILABLE. Do **not** approximate |
| `trading.net_pnl_after_charges` | Per-fill charges from broker contract notes | Show UNAVAILABLE; keep the gross label until then |
| `trading.profit_factor` | The same charges | Show UNAVAILABLE — §6.1 |
| `trading.avg_win_avg_loss` | Nothing; computable today | Surface it deliberately, with the gross caveat |
| `admin.arpu` | Payment records | Show UNAVAILABLE |

---

## 11. PH3.9 mock-removal inventory

Seventeen metrics, priority-ordered. `analytics.registry.ph39_inventory()`
returns this list programmatically, and a test asserts every entry names a
required source, a priority and a reason.

### P1 — misleading in a way that affects decisions

| # | Metric | Current implementation | Required production source | Backfill? | Recommendation |
|---|---|---|---|---|---|
| 1 | `admin.mrr` / `admin.arr` | Role counts × hardcoded ₹499/₹999 | Active subscription records (plan, price, currency, interval, status, period end) reconciled against captured payments | No | **UNAVAILABLE** until a payment provider is wired |
| 2 | `admin.revenue_today` | Count of all payment docs × ₹499 | Captured payments with amount, currency, status, `captured_at` | No | **UNAVAILABLE** |
| 3 | `admin.revenue_window_totals` | Literals `0` / `mrr` / `arr` | As above, summed per IST window | No | **UNAVAILABLE** |
| 4 | `admin.revenue_series` | `2500 + i×150 + …`, no DB access | Captured payments aggregated by IST day | **Yes** | **UNAVAILABLE** — remove the chart until real |
| 5 | `admin.payment_states` | Literals `0` | Payment status maintained by provider webhooks | No | **UNAVAILABLE**. Fix D-4 (stub refund) in the same change |
| 6 | `admin.api_health` | Hardcoded list; `status` reflects **credential configuration**, never reachability; `overall_status` is the constant `"healthy"` | `observability.health` probes + `provider_requests_total{provider,operation,outcome}` + `provider_request_duration_seconds` — **all already exist** | No | **Rewire.** This page reports healthy during a total outage |
| 7 | `admin.ai_provider_latency` | `latency_ms` 1200/900, `failures: 0`, `fallbacks: 0` | `ai_request_duration_seconds`, `ai_requests_total{provider,outcome}`, `ai_request_errors_total` — **all already exist** | No | **Rewire.** `failures: 0` beside a live failure counter hides an outage being measured |
| 8 | `admin.redis_status` | Literal `"not_configured"`; scheduler literal `"running"` | `observability.health` dependency probes | No | **Rewire.** Stale since PH2.7; stays `"running"` after the scheduler dies |
| 9 | `research.backtest.synthetic` | 20 invented trades, `randint(10,16)` wins ⟹ win rate always 50–80%, invented 2025 dates | Real historical OHLCV via the market-data gateway | No | **Fail explicitly.** A fabricated backtest is investment advice built on noise |

### P2 — wrong, but less directly actionable

| # | Metric | Current implementation | Required production source | Backfill? | Recommendation |
|---|---|---|---|---|---|
| 10 | `admin.dau` | Today's **signup** count relabelled | `db.sessions` — distinct `user_id` with `last_used_at` in the IST day | No | **Compute it.** The data already exists; only the query is missing |
| 11 | `admin.mau` | **Total** user count relabelled | `db.sessions` over a rolling 30 IST days | No | **Compute it.** DAU/MAU is currently meaningless |
| 12 | `admin.retention_rate` | Literal `78.5` | Cohort retention over `db.sessions` | **Yes** | **UNAVAILABLE** until cohorts exist |
| 13 | `admin.churn_rate` | Literal `4.2` | Cancellations / expiries over an active-subscription base | No | **UNAVAILABLE** — depends on #1 |
| 14 | `admin.growth_rate` | Literal `12.8` | Signups this period vs previous, over `db.users.created_at` | No | **Compute it.** Available today |
| 15 | `admin.feature_usage_pct` | Fixed descending literals unrelated to the counts beside them; 7 of 10 counts are also literal `0` | A feature-usage event stream (does not exist). `observability.metrics` per-route counters are the cheapest honest substitute | **Yes** | **UNAVAILABLE** for the percentage; keep the three real counts |
| 16 | `admin.ai_estimated_cost` | Messages × flat per-message rate; ambiguous currency; arbitrary 50/50 provider split | Token counts from provider responses, priced per model | No | **UNAVAILABLE** until tokens are recorded |
| 17 | `admin.ai_requests_today` | Stored chat messages (≈2× provider calls: user turn **and** assistant turn) | `ai_requests_total` from `observability.metrics` | No | **Rewire** |

### P3

`admin.arpu`, `trading.profit_factor`, `trading.avg_win_avg_loss`,
`portfolio.time_weighted_return` — see §10.4.

### Sequencing note

**#6, #7, #8 and #17 need no new data.** PH3.7 already ships real health probes
and real provider/AI metrics; those four are wiring, not instrumentation, and
they are the highest value-per-hour items in this list. Everything revenue-shaped
is blocked on a payment integration and should be one change, not five.

---

## 12. Authorization boundaries

- **Admin analytics are admin-only.** Eight admin analytics routes × three
  principals (anonymous → 401, ordinary user → 403, admin → 200) are asserted
  in `TestAdminAnalyticsAuthorization`, on top of PH3.5's mechanical
  route-table sweep which covers every admin route by construction.
- **User analytics never cross a user boundary.** Every trading, journal,
  portfolio and paper query is filtered by `user_id` at the database. Asserted
  directly: seeding another user's ₹5,000 trade leaves the caller's totals at
  zero.
- **One cross-tenant leak was found and fixed.** The end-of-day report summed
  the closed trades of the **entire platform** and sent that single figure to
  every user as "Today's P&L" — both a wrong personal number and a disclosure
  of other users' aggregate trading performance. See §13, F-2.

---

## 13. Defects found by this audit

All ten were reproduced before being fixed, and each has a test that fails on
the old code.

| ID | Sev | Defect |
|---|---|---|
| **F-2** | **P0** | `eod_report_job` **crashed on every run.** It iterated every trade and called `t.get("exit_time", "").startswith(today)`; an open trade stores `exit_time: None` *explicitly*, so `.get` returns `None` and `None.startswith` raised `AttributeError`. A broad `except` swallowed it into one log line — **no end-of-day report was ever written and no user was ever notified**, for as long as any position was open. Reproduced with a single open trade. |
| **F-2b** | **P0** | The same job's "Today's P&L" was the **platform-wide sum**, broadcast verbatim to every user. Measured: users with +₹1,000 and −₹400 were both told "+₹600". |
| **F-1** | P1 | **Paper trades contaminated every real-money statistic.** Measured: a ₹9,000 virtual gain and a ₹500 real loss reported as **+₹8,500 at a 50% win rate**. `build_intelligence` excluded paper trades from holdings and included them in realised P&L — *in the same function*. |
| **F-6** | P1 | **Partial exits were invisible to the daily loss limit.** After booking ₹500 at target 1, `realized_pnl_today` read **₹0**. A risk control, not a display bug. |
| **F-10** | P1 | **Equity-curve ranges sliced snapshot count, not calendar days.** Measured: `range=1M` returned **30 months** (2023-07 → 2025-12) on a monthly snapshot cadence. |
| **F-8** | P1 | **Portfolio return counts deposits as performance.** Measured: depositing ₹1L into a ₹1L portfolio reported **+100% return and a +100% best day**. |
| **F-11** | P1 | **The synthetic backtest was non-deterministic.** Seeded from `hash(str)`, which Python salts with `PYTHONHASHSEED` — the identical backtest returned **80% / 60% / 80% win rates on three consecutive processes**. Also structurally flattering: `randint(10, 16)` of 20 means a losing strategy cannot be represented. |
| **F-5** | P2 | **`to_list(500)` with no sort** silently truncated all-time P&L, win rate and setup statistics to an arbitrary 500 rows. |
| **F-3** | P2 | **Breakeven counted as a loss**, compounded by `reset_paper_capital` closing positions at exactly ₹0. |
| **F-4** | P2 | **Every window was a UTC day** on an IST exchange — the daily trade counter and loss budget reset at 05:30 IST. |
| **F-7** | P2 | The Dashboard labelled **lifetime unrealised P&L** as **"Today's P/L"**. |
| **F-9** | P2 | An unfetchable quote contributed ₹0 of unrealised paper P&L, indistinguishable from an unmoved position. |
| **F-18** | P2 | **Nine admin dashboard cards each carried a hardcoded "+12% vs last month"** in the gain colour — the same invented growth figure beside user counts, MRR, open tickets and broker links alike. Deleted rather than flagged: there was never anything behind it. |

---

## 14. Rules for adding an analytics metric

1. **Trace it end to end before classifying it.** Collection → service → route →
   component → pixels. An endpoint existing is not evidence.
2. **Add it to `analytics/registry.py`.** The test suite enforces this.
3. **Resolve its window through `analytics.periods`.** Never write
   `strftime("%Y-%m-%d")` and never write `.startswith(date)`.
4. **Scope trades through `analytics.queries`.** Never hand-roll an `is_paper`
   or a closed-status filter.
5. **If it cannot be computed correctly, mark it UNAVAILABLE.** Do not
   approximate, and do not return a zero.
6. **If money is involved, say `"basis": "gross"`** until charges are recorded.
7. **Do not cache it** unless invalidation is understood and staleness is
   acceptable *for that specific number*.
8. **Check the index.** If the query has no `user_id` prefix, it probably needs
   its own index and a row in `HOT_QUERIES`.

---

## 15. Related documentation

- [`OBSERVABILITY.md`](OBSERVABILITY.md) — system metrics, health probes, the
  alert catalogue. The real source for #6, #7, #8 and #17 above.
- [`../performance/PH3.4_PERFORMANCE_CERTIFICATION.md`](../performance/PH3.4_PERFORMANCE_CERTIFICATION.md)
  — the index-justification method §9.1 follows.
- [`../../.claude/MARKET_DATA_ARCHITECTURE.md`](../../.claude/MARKET_DATA_ARCHITECTURE.md)
  — provider behaviour; analytics never calls a provider directly.
- [`../testing/PH3.3_BACKEND_TEST_CERTIFICATION.md`](../testing/PH3.3_BACKEND_TEST_CERTIFICATION.md)
  — D-4, the stub refund endpoint referenced in §7 and §11.
