# Analytics Architecture & Data Integrity

**Status:** Current as of PH3.9 (2026-08-16) — **mock removal complete**
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
| **MOCK** | Fabricated — hardcoded, formula-invented, or randomised | **Nothing. No metric may carry this class.** The vocabulary is retained so a fabricated value added in future must declare itself and fail the suite, rather than blending in |
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
classification, that **no entry is classified MOCK**, that every UNAVAILABLE
entry names the production source that would answer it, and that each of the
seventeen PH3.8 mocks records what PH3.9 did to it. A mock removed without
updating its entry fails the suite; an analytics endpoint added without an entry
fails the suite; a new fabricated metric fails the suite.

The tables in §10 of this document are a **rendering** of that registry, not a
second source of truth. When they disagree, the registry is right.

### 2.2 Current totals

| Class | PH3.8 | PH3.9 |
|---|---|---|
| REAL | 4 | 4 |
| DERIVED | 26 | **32** |
| MOCK | 17 | **0** |
| UNAVAILABLE | 5 | **17** |

**There are no MOCK metrics left in the product.** PH3.8 classified seventeen
and left them in place behind a visible marker, because pulling a chart out of a
dashboard without its replacement is not an improvement. PH3.9 removed all
seventeen, and the split is the interesting part:

- **Six became real numbers** — DAU, signup growth, external API health, AI
  provider latency/failures, Redis and scheduler status. Every one was
  computable from data the platform already had.
- **Eleven became explicit UNAVAILABLE** — everything revenue-shaped, plus
  retention, churn, feature adoption, AI cost, MAU, and the synthetic backtest.

`test_analytics.py::test_no_metric_is_classified_mock` asserts the zero, and
`test_every_ph38_mock_records_what_ph39_did_to_it` names all seventeen so a
future reader can see what happened to each without archaeology.

### 2.3 The rule PH3.9 was governed by

> **Never replace mock data with fake realistic data.**

Two applications of it decided most of the sprint, and both are worth stating
because both meant *doing less* than the inventory asked:

1. **A metric is available only when the stored data can answer the question the
   metric's name asks** — not when a query returns rows. Revenue is gated on
   whether a payment integration exists (`analytics.sources.
   payments_integration`), *not* on whether `db.payments` happens to be empty.
   Gating on emptiness is how the first stray document flips revenue back to
   "available" and reports it as fact — which is the same defect PH3.8 found,
   wearing a new implementation.
2. **Where PH3.8's prescribed source could not actually answer, PH3.9 refused
   rather than approximated.** Three of its recommendations were wrong on
   inspection; see §11.1.

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
| Revenue | *(nothing)* | `db.payments` **has no writer anywhere in the codebase.** Every revenue metric is UNAVAILABLE — §7. |
| Sessions / activity | `db.sessions` (`last_used_at`) | Read by DAU since PH3.9. **Retains one refresh lifetime only** (TTL index, 7 days by default) — which bounds what it can answer; see §11.1. |
| AI & provider usage | `observability.metrics` | Real counters and histograms, read by the admin portal since PH3.9. **Process-scoped**: they reset on restart and describe one worker — §11.2. |

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

### 4.1 The contract is additive, and PH3.9 kept it that way

PH3.8 was an audit sprint: every existing flat key was preserved and an
`analytics` block added *beside* it, plus a `mock_metrics` array for consumers
that only wanted the flag.

**PH3.9 did not retire the flat keys**, though it could have. It changed their
*values* — a metric that cannot be computed now serves `null` there instead of a
fabricated number — and added `unavailable_metrics` beside `mock_metrics`. Three
reasons for keeping the shape:

- The change a consumer must absorb is already the meaningful one. Making them
  absorb a renamed envelope at the same time buries it.
- `mock_metrics` is retained and now always `[]`. That is a positive assertion
  a consumer can read — "this surface fabricates nothing" — rather than the
  absence of a field, which is indistinguishable from an old server.
- **`null` is not silently compatible.** A client doing `value || 0` gets `0`
  and shows a plausible wrong number, so the frontend routes every admin metric
  through one `MetricValue` component and tests assert the absence of `₹0`
  rather than the presence of an em-dash. See §7.2 and
  `frontend/src/components/ui/Unavailable.jsx`.

Two keys were renamed, because the old names were themselves claims the data did
not support: `ai_requests_today` → `chat_messages_today` (it counts stored
messages, both turns) and `request_count` → `message_count` on
`/api/admin/ai/usage`.

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

The gap is unchanged; what changed is that the product no longer papers over it.

> **`db.payments` has no writer anywhere in the codebase. The platform has no
> payment integration.** The collection is read by admin endpoints and indexed
> at startup. Nothing has ever written to it.

### 7.1 What was there before PH3.9

| Metric | What it actually was |
|---|---|
| `mrr` | role counts × a hardcoded ₹499/₹999 |
| `arr` | the above × 12 |
| `revenue_today` (dashboard) | **count** of all payment documents × ₹499 — not a sum, not date-filtered |
| `revenue_today` / `revenue_week` (payments) | literal `0` |
| `revenue_month` / `revenue_year` | `mrr` / `arr` |
| `pending_payments` / `refunds` / `failed_payments` | literal `0` |
| 30-day revenue series | `2500 + i×150 + (500 if i % 7 == 0)` — no database access at all |

Three things made these worse than merely imprecise:

- **`mrr` counted comped accounts as paying.** `role` is assigned by an admin
  through `POST /api/admin/users/{id}/grant-plan` with no payment involved, so
  every internal account, every beta tester and every comped user was revenue.
- **`revenue_today` read ₹0 only because the collection was empty.** The first
  record to land would have reported ₹499 of "today's revenue" whatever it was
  actually for.
- **`refunds: 0` was contradicted by the product itself.** `POST /api/admin/
  payments/{id}/refund` returned success while writing a `payment.refunded`
  audit record for a refund that never happened (PH3.5's D-4).

### 7.2 What PH3.9 did

**Every one of them now returns `null` with a reason**, resolved through
`analytics.sources`. Four decisions inside that are worth keeping:

- **The gate is `payments_integration()`, one predicate about the platform**,
  not a check on whether the collection is empty — see §2.3. The day a provider
  is wired, that is a single reviewed edit and every revenue metric becomes
  available at once.
- **The aggregation is written and tested now, not deferred.** `_sum_captured`
  sums captured payments per IST window and the accounting policy is pinned by
  tests: `created`, `pending` and `authorized` are *intents*, not revenue
  (authorized is a hold, not a capture), and `failed`/`cancelled` are neither.
  That is the classic revenue-reporting bug, and it is much cheaper to pin
  before any money exists than to discover from a finance discrepancy.
- **MRR needs strictly more than payment records.** A one-off capture is not
  recurring revenue, so summing captures over a month is not MRR. It needs
  subscription records — plan, price, currency, interval, status, period end.
  Recorded so a future sprint does not "deliver MRR" by summing captures.
- **The 30-day series is empty, not zero-filled, and is marked
  `backfillable: false`.** Thirty points at ₹0 is still a claim — that we
  measured thirty days and found no revenue — and it is false. Nor can history
  before an integration be reconstructed once one exists.

**D-4 is fixed in the same change.** The refund endpoint returns **501** and
writes **no audit record**. The audit half is the load-bearing one: a log
containing invented events is not a weaker audit log, it is a misleading one.

`_ASSUMED_PLAN_PRICE_INR` is deleted. It existed so the fabricated price was
greppable; there is no longer a fabricated price.

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
| `chat_messages {created_at}` | Chat-message day counts | Same. Neither existing compound index can serve it: they lead with `user_id`/`session_id`, and this query constrains neither |
| `sessions {last_used_at, user_id}` **(PH3.9)** | The DAU query | Nothing — none of the three existing `sessions` indexes can serve it (`session_id` and `user_id` do not constrain the field; the `expires_at` TTL index is on a different one). Without it, every admin analytics load is a full scan of a collection that grows with *logins per user*, not with users. Compound so the window filter and the distinct-user grouping are both served without touching a document |

Every one is pinned in `tests/test_perf_regression.py::HOT_QUERIES`, so removing
an index or changing a query shape fails the suite.

### 9.1.1 Query cost of the metrics PH3.9 made real

Replacing a literal with a database read is exactly how an N+1 gets introduced,
so the new queries are **counted in tests**, not assumed:

| Metric | Queries | Shape |
|---|---|---|
| `dau` | **1**, flat in session count | `$match` window → `$group` by `user_id` → `$group` count. Two `$group` stages so the *count* crosses the wire rather than the user list |
| `growth_rate` | **2** | One `count_documents` for the window, one for its preceding window |
| Every revenue metric | **0** | The integration gate short-circuits before touching the database. An admin dashboard load must not scan a collection to conclude it has no source |
| `api_health`, `ai/status`, `system/health` | **0** | Read in-process counters and cached probe results |

`admin_dashboard` also lost a query: the `list_collection_names()` guard and the
`db.payments.count_documents({})` behind it existed only to feed the fabricated
revenue figure.

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
| `GET /api/paper/pnl`, `/api/paper/trades` | One market-data call **per open position, sequentially** | A genuine N+1 over the network. Still not fixed: the fan-out helper (`real_quotes_map`) is the right tool and swapping it in touches the paper-trading write path, which was outside a mock-removal sprint's scope too. **Carried forward as debt** — it is a latency defect, not a data-integrity one. |

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
| Backtest metrics | `POST /api/backtest` | Real historical bars only. **There is no fallback path**: missing history is a 503, never an invented result |
| Conversion rate | `GET /api/admin/analytics/users` | Conversion to a **granted role**, not to a paid plan |
| Top AI users | `GET /api/admin/ai/usage` | The count is real; the cost beside it is not |

### 10.3 MOCK

**Empty.** See §11 for what each of the seventeen became.

### 10.4 UNAVAILABLE

Seventeen metrics. The five PH3.8 identified, plus the twelve PH3.9 could not
honestly compute. Each names the production data that would make it answerable;
`analytics.registry.ph39_inventory()` returns the list programmatically and a
test asserts every entry carries a required source, a priority and a reason.

| Metric | Blocked on | Backfillable? |
|---|---|---|
| `admin.mrr`, `admin.arr` | Active subscription records (plan, price, currency, interval, status, period end) | No |
| `admin.revenue_today`, `admin.revenue_window_totals` | Captured payment records, summed per IST window | No |
| `admin.revenue_series` | The same, aggregated by IST day | **No** — history before an integration cannot be reconstructed |
| `admin.payment_states` | Payment status maintained by provider webhooks | No |
| `admin.arpu` | Captured payments ÷ active users; both terms missing | No |
| `admin.churn_rate` | Cancellations over an active-subscription base | No |
| `admin.mau` | A durable per-user activity record outliving the session TTL | No |
| `admin.retention_rate` | Per-user activity history spanning weeks, plus a durable "first seen" | **No** — an event never written cannot be reconstructed |
| `admin.feature_usage_pct` | A feature-usage event stream (feature, user_id, timestamp) | **No** |
| `admin.ai_estimated_cost` | Token counts from provider responses, priced per model | No |
| `research.backtest.synthetic` | Real historical OHLCV — the endpoint now fails explicitly | n/a |
| `portfolio.time_weighted_return` | A dated external cash-flow ledger | **No** |
| `trading.net_pnl_after_charges` | Per-fill charges from broker contract notes | No |
| `trading.profit_factor` | The same charges — §6.1 | No |
| `trading.avg_win_avg_loss` | Nothing; computable today. Needs a surface and the gross caveat | n/a |

---

## 11. What PH3.9 did to each of the seventeen

`analytics.registry.ph39_resolutions()` returns this programmatically, and
`test_every_ph38_mock_records_what_ph39_did_to_it` asserts that all seventeen
carry a resolution. The registry is authoritative; this is a rendering of it.

### Became real numbers (6)

| # | Metric | Now | Source |
|---|---|---|---|
| 6 | `admin.api_health` | DERIVED | `observability.health` probes + `provider_requests_total` / `provider_request_duration_seconds` |
| 7 | `admin.ai_provider_latency` | DERIVED | `ai_requests_total{provider,outcome}`, `ai_request_errors_total`, `ai_request_duration_seconds` |
| 8 | `admin.redis_status` | DERIVED | The registered Redis readiness probe; the scheduler asked directly |
| 10 | `admin.dau` | DERIVED | `db.sessions.last_used_at` — distinct users in the IST day |
| 14 | `admin.growth_rate` | DERIVED | `db.users.created_at`, this window vs `periods.preceding(window)` |
| — | `admin.dashboard_health_badges` | DERIVED | Real probes; `api_health: "healthy"` deleted |

### Became explicit UNAVAILABLE (11)

| # | Metric | Why not computed |
|---|---|---|
| 1 | `admin.mrr` / `admin.arr` | No subscription records — and MRR needs more than payments |
| 2 | `admin.revenue_today` | No captured payment records |
| 3 | `admin.revenue_window_totals` | The same |
| 4 | `admin.revenue_series` | The same; series is **empty**, not zero-filled |
| 5 | `admin.payment_states` | No payment status source. D-4 fixed alongside |
| 9 | `research.backtest.synthetic` | Deleted; 503 on missing history |
| 11 | `admin.mau` | Session TTL truncates the window — §11.1 |
| 12 | `admin.retention_rate` | No retained activity history; not back-fillable |
| 13 | `admin.churn_rate` | Depends on #1 |
| 15 | `admin.feature_usage_pct` | No feature-usage event stream; seven zero rows deleted |
| 16 | `admin.ai_estimated_cost` | No token accounting |

### 11.1 Three departures from the PH3.8 inventory

PH3.8's recommendations were written from the audit's vantage point. Three did
not survive contact with the source, and following them would each have
replaced a fabricated number with a *systematically wrong* one — which is worse,
because a wrong number that came from a real query is much harder to spot.

**#11 `admin.mau` — "compute it from `db.sessions` over a rolling 30 IST days".**
`db.sessions` carries a TTL index that deletes a session at `last_used_at +
JWT_REFRESH_TTL_SECONDS` (seven days by default). A 30-day window asks for rows
the database has already removed, so the query returns a **7-day count under a
30-day label**, undercounting more the longer ago a user churned.
`analytics.sources.active_users` checks the window against the retention horizon
and refuses it. The refusal is self-correcting: raise the refresh TTL past
thirty days and the same call starts returning a value, because the data really
would be there. DAU is unaffected — a single day sits far inside the horizon.

**#6 `admin.api_health` — "rewire".** Not a straight rewire, because the row
list itself was unanswerable. The old table named *vendors* — Yahoo Finance,
Alpha Vantage — with individual latencies, and the Market Gateway's Source
Manager picks an upstream per request with that choice deliberately invisible
above the gateway (`MARKET_DATA_ARCHITECTURE.md`). `instruments.PROVIDERS` is a
closed vocabulary of **logical** providers for exactly that reason, and only
`market_data` and `news` have instrumentation call sites at all. So the page now
reports logical providers, states plainly which integrations are `not_measured`,
and reports `configured` as its own column rather than as evidence of health.
**The Razorpay row was deleted outright** — it reported `status: "configured"`
beside a 300ms latency for an integration that exists nowhere in this codebase.

**#17 `admin.ai_requests_today` — "rewire to `ai_requests_total`".** That would
trade a durable database count for an in-process counter that resets on every
deploy and, on a multi-worker deployment, covers one worker of N — a worse
source for a figure labelled "today". The field was **renamed** to
`chat_messages_today` instead, which is what it always counted (both the user
turn and the assistant turn, so it overstated provider calls by roughly 2×). The
value was always correct; only the claim was wrong. Real provider counts are
reported separately as `*_since_start`, where the process scope is stated.

### 11.2 Two labelling rules the counter-backed metrics enforce

Both exist because the honest source has a property the fabricated one did not.

- **Counters are process-scoped.** They answer "since this process started",
  never "today". Every counter-derived field is named `*_since_start` and ships
  with a `scope` block carrying `process_uptime_seconds` and the restart caveat.
  A test asserts no field produced by `analytics.platform_health` contains the
  substring `today`.
- **Latency is a p95 bucket bound, never a mean.** `sum / count` is the number
  that hides an outage: ninety-nine 10ms calls and one 10s call average to 110ms
  and every dashboard looks calm. `_latency_bound` reports the stored bucket
  boundary at or below which 95% of observations fell — a true bound, not an
  interpolation inventing precision the histogram does not have. **No traffic
  returns `None`, never `0`**: "instantaneous" and "never measured" are opposite
  facts.

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

### 13.1 Defects PH3.9 fixed

Mock removal was the mandate; these came out of it.

| ID | Sev | Defect |
|---|---|---|
| **D-4** | **HIGH** | **The refund endpoint wrote audit records for refunds that never happened.** Carried since PH3.5. `POST /api/admin/payments/{id}/refund` read no payment, called no provider, and returned `{"success": true}` for *any* string, while appending `payment.refunded` to the immutable audit log. Two harms, and the second is worse: an operator was told a customer had been refunded, and the artefact whose entire purpose is to record what happened was recording an event that did not. Now **501, and no audit record.** |
| **F-19** | P1 | **`admin.api_health` reported an integration that does not exist.** The hardcoded list carried a Razorpay row at `status: "configured"` with a 300ms latency; there is no payment integration anywhere in the codebase. Row deleted. |
| **F-20** | P1 | **The backtest fallback was reached on *any* exception**, not only a missing library — so a transient network blip, a rate limit or a delisted symbol produced a flattering fabricated result rather than an error. The `except Exception` that swallowed it is gone with the fallback. |
| **F-21** | P2 | **`server_health` and `api_health` were literals that checked nothing.** They read `"healthy"` during a total outage. `api_health` is deleted; `server_health` is `"serving"` — the only claim a health field served *by* the process can honestly make about that process. |
| **F-22** | P2 | **Seven feature-usage rows reported `usage_count: 0` as a literal.** Nothing counts the scanner, morning report, portfolio, news, SIP advisor, paper trading or backtesting. "0 uses" is a measurement claim; the rows are deleted rather than zeroed. |
| **F-23** | P2 | **A per-user AI cost was rendered with a dollar sign over an INR-denominated product**, from rates that read as USD, against per-token billing. Column removed. |

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
6. **Ask whether the stored data answers the question the metric's NAME asks** —
   not merely whether a query returns rows. This is the rule that caught MAU:
   the query runs, and the answer is a 7-day count wearing a 30-day label.
   Where a source has a retention horizon, a sampling scope or a proxy
   relationship to what you are claiming, that constraint belongs in the code as
   a gate, not in a comment.
7. **Never label a counter-derived figure with a calendar window.** In-process
   counters reset on restart and describe one worker; name them `*_since_start`
   and ship the scope. Never average a latency histogram — report a bucket
   bound, and report `None` for no observations.
8. **If money is involved, say `"basis": "gross"`** until charges are recorded.
9. **Do not cache it** unless invalidation is understood and staleness is
   acceptable *for that specific number*.
10. **On the frontend, route it through `MetricValue`.** Never `value || 0`:
    `null` means unavailable and `0` is a measurement, and one falsy check turns
    the first into the second in the same typeface as a real figure.
11. **Check the index.** If the query has no `user_id` prefix, it probably needs
   its own index and a row in `HOT_QUERIES`.

---

## 15. Related documentation

- [`OBSERVABILITY.md`](OBSERVABILITY.md) — system metrics, health probes, the
  alert catalogue. The real source now read by the four rewired metrics in §11,
  and the place to understand the process-scope caveat in §11.2.
- [`../performance/PH3.4_PERFORMANCE_CERTIFICATION.md`](../performance/PH3.4_PERFORMANCE_CERTIFICATION.md)
  — the index-justification method §9.1 follows.
- [`../../.claude/MARKET_DATA_ARCHITECTURE.md`](../../.claude/MARKET_DATA_ARCHITECTURE.md)
  — provider behaviour; analytics never calls a provider directly. **The reason
  §11.1 gives for refusing a per-vendor API-health breakdown.**
- [`../testing/PH3.3_BACKEND_TEST_CERTIFICATION.md`](../testing/PH3.3_BACKEND_TEST_CERTIFICATION.md)
  — where D-4, the stub refund endpoint, was found. Fixed in PH3.9; see §13.1.

### Where the code lives

| Module | Owns |
|---|---|
| `backend/analytics/registry.py` | The classification of every metric, and what PH3.9 did to each of the seventeen |
| `backend/analytics/sources.py` | The two gates — payment integration, session retention horizon — and the DB-backed metrics behind them |
| `backend/analytics/platform_health.py` | Health and provider metrics read from real probes and counters |
| `backend/analytics/periods.py` | Windows, and `preceding()` for period-over-period comparison |
| `frontend/src/components/ui/Unavailable.jsx` | The `null` vs `0` rule, expressed once |
| `backend/tests/test_ph39_mock_removal.py` | The units above, plus counter-tests naming each removed formula |
| `backend/tests/test_analytics.py` | Endpoint-level removal assertions and the registry invariants |
