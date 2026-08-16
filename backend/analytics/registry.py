"""The analytics inventory, as code (PH3.8).

WHY THIS IS A PYTHON MODULE AND NOT A TABLE IN A MARKDOWN FILE
--------------------------------------------------------------
An inventory of "which of our numbers are real" is worth exactly as much as its
accuracy on the day somebody reads it. A markdown table is accurate on the day
it is written and drifts silently forever after: a metric gets added, an
endpoint gets renamed, a mock gets replaced, and nothing anywhere fails.

This registry is imported by ``tests/test_analytics.py``, which asserts that
every endpoint listed here still exists on the live route table, that every
entry carries a valid provenance class, and that every ``MOCK`` entry names the
production source that would replace it. A mock removed in PH3.9 without
updating its entry fails the suite; an analytics endpoint added without an entry
fails the suite. The markdown document (``docs/architecture/ANALYTICS.md``) is
generated *from* this, not maintained alongside it.

READING AN ENTRY
----------------
``provenance`` is the classification. The rule applied throughout — and the one
worth restating, because it is the rule that reclassified most of this file —
is that **an endpoint existing is not evidence a metric is real.** Each entry
below was traced end to end: collection → repository/service → route → frontend
component → the pixels a user sees. Where that trace ends at a literal, a
formula over a proxy, or a collection nothing writes to, the entry says so.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from analytics.contract import DERIVED, MOCK, PROVENANCE, REAL, UNAVAILABLE


@dataclass(frozen=True)
class MetricSpec:
    """One inventoried analytics number."""

    key: str                      # stable identifier, `surface.metric`
    label: str                    # what a human calls it
    surface: str                  # admin | trading | portfolio | paper | journal | research
    endpoint: str                 # the API route that serves it
    provenance: str               # REAL | DERIVED | MOCK | UNAVAILABLE
    source: str                   # collection(s) / external system of record
    calculation: str              # how the number is produced
    window: str                   # the time window, in `analytics.periods` keys
    consumer: str                 # the frontend that renders it
    audience: str = "user"        # user | admin
    note: str = ""                # anything a reader needs to interpret it
    #: The production data a metric needs to be answerable. Required on every
    #: MOCK and UNAVAILABLE entry — a metric declared unanswerable without
    #: naming what would answer it is a complaint, not a handoff.
    required_source: str = ""
    backfill_required: bool = False
    prefer_unavailable: bool = False   # show UNAVAILABLE rather than invent a value
    priority: str = ""            # P1 | P2 | P3
    defect: str = ""              # PH3.8 finding id, when one applies
    #: PH3.9 — what the mock-removal sprint actually did to this metric, and why.
    #: Present on every entry PH3.8 classified MOCK. Two of them record a
    #: DEPARTURE from PH3.8's prescription, which is the reason this field is
    #: prose and not a boolean: "removed" is not the interesting part, "removed
    #: and replaced with UNAVAILABLE because the prescribed source could not
    #: actually answer the question" is.
    ph39_resolution: str = ""


def _spec(**kw) -> MetricSpec:
    return MetricSpec(**kw)


# --------------------------------------------------------------------------- #
# TRADING — the user's own realised and open positions.
# Source of truth: `db.trades`. Broker fills are the upstream authority; this
# collection is the platform's record of them.
# --------------------------------------------------------------------------- #
_TRADING = [
    _spec(
        key="trading.total_pnl", label="Total P&L", surface="trading",
        endpoint="GET /api/trades/pnl", provenance=DERIVED, source="db.trades.pnl",
        calculation="Σ pnl over trades with pnl set (i.e. fully closed).",
        window="all", consumer="TradeMonitor, Dashboard",
        note="Partial exits are invisible: `pnl` is written only at full close, so "
             "profit already booked at target 1 is missing until the last share is "
             "sold. `realized_pnl` on the trade document holds it (F-6).",
        defect="F-2, F-6",
    ),
    _spec(
        key="trading.today_pnl", label="Today's realised P&L", surface="trading",
        endpoint="GET /api/trades/pnl", provenance=DERIVED, source="db.trades.pnl/exit_time",
        calculation="Σ pnl over trades whose exit_time falls in the IST day.",
        window="today", consumer="TradeMonitor",
        note="Boundary corrected from the UTC day to the IST day in PH3.8 (F-4).",
        defect="F-4",
    ),
    _spec(
        key="trading.win_rate", label="Win rate", surface="trading",
        endpoint="GET /api/trades/pnl", provenance=DERIVED, source="db.trades.pnl",
        calculation="wins / (wins + losses) × 100, where a win is pnl > 0, a loss is "
                    "pnl < 0, and a breakeven trade (pnl == 0) is counted as neither.",
        window="all", consumer="TradeMonitor",
        note="Breakeven trades were scored as losses before PH3.8 (F-3).",
        defect="F-3",
    ),
    _spec(
        key="trading.open_trades", label="Open positions", surface="trading",
        endpoint="GET /api/trades/pnl", provenance=REAL, source="db.trades.status",
        calculation="count of status == OPEN.", window="all", consumer="TradeMonitor",
    ),
    _spec(
        key="trading.risk.realized_pnl_today", label="Realised P&L today", surface="trading",
        endpoint="GET /api/trades/risk/summary", provenance=DERIVED,
        source="db.trades.pnl/exit_time",
        calculation="Σ pnl over non-paper trades closed in the IST day.",
        window="today", consumer="Risk Manager panel",
        note="Feeds the max_daily_loss trading halt, so its correctness is a RISK "
             "CONTROL, not only a display concern.",
        defect="F-4, F-6",
    ),
    _spec(
        key="trading.risk.open_risk", label="Open risk", surface="trading",
        endpoint="GET /api/trades/risk/summary", provenance=DERIVED,
        source="db.trades entry_price/stop_loss/quantity_open",
        calculation="Σ max(0, entry − stop) × quantity_open, side-aware.",
        window="all", consumer="Risk Manager panel",
        note="The reference implementation for trade analytics in this codebase: "
             "paper-filtered, short-aware, and based on quantity_open rather than "
             "the original quantity.",
    ),
    _spec(
        key="trading.risk.open_exposure", label="Open exposure", surface="trading",
        endpoint="GET /api/trades/risk/summary", provenance=DERIVED,
        source="db.trades entry_price/quantity_open",
        calculation="Σ entry_price × quantity_open.", window="all",
        consumer="Risk Manager panel",
        note="Exposure at COST, not at market. Named accordingly; it is not a "
             "position value.",
    ),
]

# --------------------------------------------------------------------------- #
# JOURNAL — trade-by-trade performance history and coaching.
# --------------------------------------------------------------------------- #
_JOURNAL = [
    _spec(
        key="journal.recent.win_rate", label="Win rate (period)", surface="journal",
        endpoint="GET /api/journal/stats", provenance=DERIVED, source="db.trades",
        calculation="wins / closed trades × 100 over the window.",
        window="7d", consumer="TradeJournal",
        note="Paper trades were included before PH3.8, so a virtual-money win "
             "inflated a real-money statistic (F-1).",
        defect="F-1",
    ),
    _spec(
        key="journal.all_time.total_pnl", label="All-time P&L", surface="journal",
        endpoint="GET /api/journal/stats", provenance=DERIVED, source="db.trades.pnl",
        calculation="Σ pnl over all closed non-paper trades.",
        window="all", consumer="TradeJournal",
        note="Bounded by a 500-document fetch before PH3.8, silently truncating "
             "the all-time figure for an active trader (F-5).",
        defect="F-1, F-5",
    ),
    _spec(
        key="journal.setup_success_rates", label="Win rate by setup", surface="journal",
        endpoint="GET /api/journal/setup-stats", provenance=DERIVED,
        source="db.trades.setup_type/pnl",
        calculation="Per setup_type: wins / trades × 100, average pnl_percent, best "
                    "and worst pnl. Untagged trades are excluded, not bucketed.",
        window="all", consumer="TradeJournal",
        note="Already returns an explicit empty state with a reason rather than a "
             "table of zeros — the pattern the rest of this inventory was measured "
             "against.",
        defect="F-1",
    ),
]

# --------------------------------------------------------------------------- #
# PAPER TRADING — virtual capital. Real arithmetic over real quotes; the money
# is fictional BY DESIGN, which is a different thing from a fabricated metric.
# --------------------------------------------------------------------------- #
_PAPER = [
    _spec(
        key="paper.realized_pnl", label="Realised P&L (paper)", surface="paper",
        endpoint="GET /api/paper/pnl", provenance=DERIVED,
        source="db.trades where is_paper",
        calculation="Σ pnl over closed paper trades.", window="all",
        consumer="PaperTrading",
        note="Virtual capital. Correctly isolated from real-money analytics as of "
             "PH3.8 — the isolation runs in both directions.",
    ),
    _spec(
        key="paper.unrealized_pnl", label="Unrealised P&L (paper)", surface="paper",
        endpoint="GET /api/paper/pnl", provenance=DERIVED,
        source="db.trades where is_paper + live quotes",
        calculation="Σ side-aware (mark − entry) × quantity over open paper trades.",
        window="all", consumer="PaperTrading",
        note="A quote that cannot be fetched contributes zero rather than failing "
             "the response, so this figure understates when the market feed is "
             "degraded (F-9, carried).",
        defect="F-9",
    ),
    _spec(
        key="paper.total_pnl_pct", label="Return % (paper)", surface="paper",
        endpoint="GET /api/paper/pnl", provenance=DERIVED, source="db.trades, constant",
        calculation="total_pnl / 100,000 × 100 — the FIXED starting capital, not the "
                    "user's current balance.",
        window="all", consumer="PaperTrading",
        note="Denominator is the constant starting capital by design: it makes the "
             "figure a return on the account's inception, which is the only "
             "denominator that stays stable as positions open and close.",
    ),
]

# --------------------------------------------------------------------------- #
# PORTFOLIO — broker-primary holdings merged with manual open trades.
# --------------------------------------------------------------------------- #
_PORTFOLIO = [
    _spec(
        key="portfolio.current_value", label="Portfolio value", surface="portfolio",
        endpoint="GET /api/portfolio/summary", provenance=DERIVED,
        source="db.holdings (broker) + db.trades (manual) + live quotes",
        calculation="Σ live mark × quantity; falls back to the last real broker mark, "
                    "then to invested basis. Never a fabricated price.",
        window="all", consumer="Portfolio, Dashboard",
    ),
    _spec(
        key="portfolio.unrealized", label="Unrealised P&L", surface="portfolio",
        endpoint="GET /api/portfolio/summary", provenance=DERIVED,
        source="db.holdings + db.trades + live quotes",
        calculation="current_value − invested.", window="all",
        consumer="Portfolio, Dashboard",
        note="The Dashboard renders this under the label \"Today's P/L\". It is "
             "lifetime unrealised P&L, not a daily figure (F-7).",
        defect="F-7",
    ),
    _spec(
        key="portfolio.realized", label="Realised P&L", surface="portfolio",
        endpoint="GET /api/portfolio/summary", provenance=DERIVED, source="db.trades.pnl",
        calculation="Σ pnl over closed non-paper trades.", window="all",
        consumer="Portfolio",
        note="Included paper trades before PH3.8 while the holdings half of the same "
             "function excluded them — one payload, two definitions of 'my "
             "portfolio' (F-1).",
        defect="F-1, F-5",
    ),
    _spec(
        key="portfolio.diversification_hhi", label="Concentration (HHI)", surface="portfolio",
        endpoint="GET /api/portfolio/intelligence", provenance=DERIVED,
        source="derived holdings",
        calculation="Herfindahl-Hirschman index Σ wᵢ² over portfolio weights; "
                    "effective holdings = 1/HHI.",
        window="all", consumer="Portfolio",
    ),
    _spec(
        key="portfolio.risk_score", label="Risk score", surface="portfolio",
        endpoint="GET /api/portfolio/intelligence", provenance=DERIVED,
        source="derived holdings + monitor alerts",
        calculation="Additive 0–100 over named factors (concentration, sector, "
                    "diversification, drawdown, at-risk positions, RSI).",
        window="all", consumer="Portfolio",
        note="A HEURISTIC with declared weights, not a market-risk model. Every "
             "point traces to a factor shown to the user, which is what keeps it "
             "honest — it is not presented as a VaR or a volatility measure.",
    ),
    _spec(
        key="portfolio.dividend_income", label="Est. annual dividend income",
        surface="portfolio", endpoint="GET /api/portfolio/intelligence",
        provenance=DERIVED, source="live trailing dividend rates × quantity",
        calculation="Σ trailing annual rate × quantity, for symbols with real rates.",
        window="all", consumer="Portfolio",
        note="Returns available:false when no real rate exists for any holding. "
             "Never invents a yield.",
    ),
    _spec(
        key="portfolio.pct_return", label="Return over period", surface="portfolio",
        endpoint="GET /api/portfolio/performance", provenance=DERIVED,
        source="db.portfolio_snapshots",
        calculation="(last value − first value) / first value × 100 over stored "
                    "end-of-day snapshots.",
        window="30d", consumer="Portfolio",
        note="NOT flow-adjusted. Capital added during the window is indistinguishable "
             "from a gain: depositing and investing ₹1L into a ₹1L portfolio reports "
             "+100%. A true time-weighted return needs per-day external-flow records "
             "the platform does not capture (F-8).",
        defect="F-8",
    ),
    _spec(
        key="portfolio.equity_curve", label="Equity curve", surface="portfolio",
        endpoint="GET /api/portfolio/performance", provenance=DERIVED,
        source="db.portfolio_snapshots",
        calculation="Stored end-of-day snapshots, ascending, sliced to the window.",
        window="30d", consumer="Portfolio",
        note="Built forward from real marks — never back-filled. Returns "
             "available:false below two snapshots. Range keys sliced by snapshot "
             "COUNT rather than calendar span before PH3.8, so '1M' could return "
             "30 months (F-10).",
        defect="F-10",
    ),
]

# --------------------------------------------------------------------------- #
# RESEARCH — backtesting and single-stock analytics.
# --------------------------------------------------------------------------- #
_RESEARCH = [
    _spec(
        key="research.backtest.win_rate", label="Backtest win rate", surface="research",
        endpoint="POST /api/backtest", provenance=DERIVED,
        source="yfinance daily OHLCV",
        calculation="Strategy simulated over real historical bars; wins / trades.",
        window="all", consumer="Backtesting",
        note="DERIVED **only on the yfinance path**. The fallback path is a separate "
             "registry entry and is MOCK.",
    ),
    _spec(
        key="research.backtest.synthetic", label="Backtest (synthetic fallback)",
        surface="research", endpoint="POST /api/backtest", provenance=UNAVAILABLE,
        source="(deleted — no fallback path exists)",
        calculation="None. `_synthetic_backtest` was deleted; the engine raises "
                    "HistoricalDataUnavailable and the route answers 503.",
        window="all", consumer="Backtesting",
        note="WAS 20 invented trades with a win count drawn from randint(10, 16), so "
             "the win rate was always 50–80% and A LOSING STRATEGY COULD NOT BE "
             "REPRESENTED, over an invented price series with invented 2025 dates — "
             "then passed through the SAME _compute_metrics the real path uses, so "
             "the Sharpe ratio, drawdown and return arrived looking like measured "
             "statistics and rendered in the same cards. It was reached on ANY "
             "yfinance failure (missing library, network blip, rate limit, delisted "
             "symbol), so the common case of a brief provider outage produced a "
             "flattering fabricated result rather than an error. The seed was "
             "`hash(str)`, PYTHONHASHSEED-salted, so it was not even reproducible: "
             "80% / 60% / 80% across three runs of identical input.",
        required_source="Real historical OHLCV.",
        prefer_unavailable=True, priority="P1", defect="F-11",
        ph39_resolution="DELETED. The most dangerous of the seventeen, because a "
                        "fabricated backtest is investment advice built on noise. "
                        "There is no honest fallback — a backtest without historical "
                        "prices is not a degraded backtest, it is not a backtest. "
                        "503 rather than 500: the request was valid and an upstream "
                        "data source is unavailable, which is retryable.",
    ),
    _spec(
        key="research.stock.max_drawdown", label="Max drawdown (1Y)", surface="research",
        endpoint="GET /api/stocks/{symbol}/risk", provenance=DERIVED,
        source="live historical closes",
        calculation="Worst peak-to-trough decline over the trailing year.",
        window="all", consumer="StockDetail",
    ),
]

# --------------------------------------------------------------------------- #
# ADMIN — platform and business analytics. The densest concentration of mock
# data in the product, and the reason PH3.9 exists.
# --------------------------------------------------------------------------- #
_ADMIN = [
    _spec(
        key="admin.total_users", label="Total users", surface="admin",
        endpoint="GET /api/admin/dashboard", provenance=REAL, source="db.users",
        calculation="count_documents({}).", window="all", consumer="AdminDashboard",
        audience="admin",
    ),
    _spec(
        key="admin.premium_users", label="Pro users", surface="admin",
        endpoint="GET /api/admin/dashboard", provenance=REAL, source="db.users.role",
        calculation="count of role in (pro, premium).", window="all",
        consumer="AdminDashboard", audience="admin",
        note="A count of ROLES, which is real. It becomes fabricated only when "
             "multiplied by a price to imply revenue — see admin.mrr.",
    ),
    _spec(
        key="admin.today_trades", label="Trades today", surface="admin",
        endpoint="GET /api/admin/dashboard", provenance=DERIVED, source="db.trades.entry_time",
        calculation="count of trades entered in the IST day.", window="today",
        consumer="AdminDashboard", audience="admin",
        note="Includes paper trades, so it is platform ACTIVITY, not order flow.",
        defect="F-4",
    ),
    _spec(
        key="admin.chat_messages_today", label="Chat messages today", surface="admin",
        endpoint="GET /api/admin/dashboard, GET /api/admin/ai/status",
        provenance=DERIVED, source="db.chat_messages.created_at",
        calculation="count of chat messages created in the IST day.", window="today",
        consumer="AdminDashboard, AdminAI", audience="admin",
        note="RENAMED in PH3.9 from `ai_requests_today`, which it never was: it counts "
             "stored messages, and a message is written for both the user turn and "
             "the assistant turn, so the old name overstated provider calls by "
             "roughly 2x. The value was always correct — only the claim was wrong.",
        defect="F-12",
        ph39_resolution="RENAMED, not rewired. PH3.8's inventory listed this as item "
                        "#17, 'rewire to ai_requests_total'. That would have traded a "
                        "durable, restart-surviving database count for an in-process "
                        "counter that resets on deploy and, on a multi-worker "
                        "deployment, covers one worker — a worse source for a figure "
                        "labelled 'today'. Real provider call counts are reported "
                        "separately on GET /api/admin/ai/status as *_since_start, "
                        "where the process scope is stated.",
    ),
    _spec(
        key="admin.revenue_today", label="Revenue today", surface="admin",
        endpoint="GET /api/admin/dashboard", provenance=UNAVAILABLE,
        source="(no payment records)",
        calculation="Σ captured payment amounts in the IST day — implemented in "
                    "`analytics.sources._sum_captured`, gated on a payment "
                    "integration that does not exist.",
        window="today", consumer="AdminDashboard", audience="admin",
        note="WAS: total payment documents × ₹499 — not a sum, not date-filtered, "
             "blind to amount, currency, status and refunds. It rendered ₹0 only "
             "because `db.payments` is empty, so the first record to land would have "
             "reported ₹499 of 'today's revenue' whatever it was for.",
        required_source="Verified payment records with amount, currency, status and "
                        "captured_at, written by the payment provider webhook.",
        prefer_unavailable=True, priority="P1", defect="F-13",
        ph39_resolution="REMOVED → UNAVAILABLE. The gate is `analytics.sources."
                        "payments_integration()`, a predicate about the PLATFORM, not "
                        "about whether the collection happens to be empty — gating on "
                        "emptiness is how the first stray document flips revenue back "
                        "to 'available' and reports it as fact.",
    ),
    _spec(
        key="admin.mrr", label="MRR", surface="admin",
        endpoint="GET /api/admin/dashboard, GET /api/admin/payments/stats",
        provenance=UNAVAILABLE, source="(no subscription records)",
        calculation="Requires active subscription records; none exist.",
        window="all", consumer="AdminDashboard, AdminPayments", audience="admin",
        note="WAS: pro/premium count × ₹499 + elite count × ₹999. Revenue inferred "
             "from role assignment — and roles are granted by an admin through "
             "POST /api/admin/users/{id}/grant-plan with no payment involved, so "
             "every comped, internal and beta account counted as paying. The prices "
             "were literals in the route, not the plan the user bought.",
        required_source="Active subscription records (plan, price, currency, billing "
                        "interval, status, current_period_end) reconciled against "
                        "captured payments.",
        prefer_unavailable=True, priority="P1", defect="F-13",
        ph39_resolution="REMOVED → UNAVAILABLE. Note this needs MORE than payment "
                        "records: a one-off capture is not recurring revenue, so "
                        "summing captures over a month is not MRR.",
    ),
    _spec(
        key="admin.arr", label="ARR", surface="admin",
        endpoint="GET /api/admin/dashboard, GET /api/admin/payments/stats",
        provenance=UNAVAILABLE, source="(no subscription records)",
        calculation="MRR × 12; MRR is unavailable.", window="all",
        consumer="AdminDashboard, AdminPayments", audience="admin",
        note="WAS: MRR × 12, inheriting every defect of admin.mrr and multiplying it.",
        required_source="As admin.mrr.", prefer_unavailable=True, priority="P1",
        defect="F-13",
        ph39_resolution="REMOVED → UNAVAILABLE, with admin.mrr.",
    ),
    _spec(
        key="admin.revenue_series", label="Revenue trend (30d)", surface="admin",
        endpoint="GET /api/admin/analytics/revenue", provenance=UNAVAILABLE,
        source="(no payment records)",
        calculation="Captured payments aggregated by IST day; no such records exist.",
        window="30d", consumer="AdminAnalytics", audience="admin",
        note="WAS the most misleading artefact in the product: revenue = 2500 + "
             "(i × 150) + (500 if i %% 7 == 0), subscriptions = 3 + (i %% 5), with NO "
             "database access of any kind — a smooth, always-up-and-to-the-right "
             "chart rendered by Recharts with no visual distinction from a real "
             "series, on an installation that has never processed a payment.",
        required_source="Captured payments aggregated by IST day.",
        backfill_required=True, prefer_unavailable=True, priority="P1", defect="F-13",
        ph39_resolution="REMOVED → UNAVAILABLE, and the series is EMPTY rather than "
                        "zero-filled. Thirty points at ₹0 is still a claim — that we "
                        "measured thirty days and found no revenue — and it is false. "
                        "Explicitly not back-fillable: history before an integration "
                        "exists cannot be reconstructed.",
    ),
    _spec(
        key="admin.revenue_window_totals", label="Revenue today/week/month/year",
        surface="admin", endpoint="GET /api/admin/payments/stats",
        provenance=UNAVAILABLE, source="(no payment records)",
        calculation="Σ captured payments per IST window; implemented and gated.",
        window="all", consumer="AdminPayments", audience="admin",
        note="WAS: revenue_today = 0, revenue_week = 0 (hardcoded zeros a reader "
             "cannot distinguish from a genuine no-revenue day), revenue_month = MRR "
             "and revenue_year = ARR — a monthly revenue figure that was really a "
             "role count × a literal price.",
        required_source="Captured payments, summed per window.",
        prefer_unavailable=True, priority="P1", defect="F-13",
        ph39_resolution="REMOVED → UNAVAILABLE (all four windows). The aggregation "
                        "that will serve them is written and tested now, including "
                        "that created/pending/authorized/failed are not revenue.",
    ),
    _spec(
        key="admin.payment_states", label="Pending / refunded / failed payments",
        surface="admin", endpoint="GET /api/admin/payments/stats",
        provenance=UNAVAILABLE, source="(no payment records)",
        calculation="Counts by payment status; no payment records exist.",
        window="all", consumer="AdminPayments", audience="admin",
        note="WAS: three literal zeros. `refunds: 0` was additionally contradicted by "
             "the product itself — PH3.5's D-4 found POST /api/admin/payments/{id}/"
             "refund returning success and writing a `payment.refunded` AUDIT RECORD "
             "for a refund that never happened.",
        required_source="Payment records with a status field maintained by provider "
                        "webhooks.",
        prefer_unavailable=True, priority="P1", defect="F-13, D-4",
        ph39_resolution="REMOVED → UNAVAILABLE, and D-4 fixed in the same change: the "
                        "refund endpoint now returns 501 and writes NO audit record. "
                        "An audit log containing invented events is not a weaker "
                        "audit log, it is a misleading one.",
    ),
    _spec(
        key="admin.dau", label="Daily active users", surface="admin",
        endpoint="GET /api/admin/analytics/users", provenance=DERIVED,
        source="db.sessions.last_used_at",
        calculation="Distinct user_id whose session was created or refreshed inside "
                    "the IST day, via two $group stages so the count crosses the "
                    "wire rather than the user list.",
        window="today", consumer="AdminAnalytics", audience="admin",
        note="WAS today's SIGNUP count relabelled — a different population, differing "
             "by orders of magnitude on a mature product. `last_used_at` advances on "
             "every refresh-token rotation and access tokens live 15 minutes, so "
             "within a day it is a faithful activity signal. A signed-in user who "
             "made no request in the window is correctly not counted.",
        defect="F-14",
        ph39_resolution="REPLACED with a real computation over db.sessions (PH1.6), "
                        "which has existed the whole time. New index "
                        "{last_used_at, user_id}.",
    ),
    _spec(
        key="admin.mau", label="Monthly active users", surface="admin",
        endpoint="GET /api/admin/analytics/users", provenance=UNAVAILABLE,
        source="(no retained activity history)",
        calculation="Distinct active user_id over 30 IST days — refused, because the "
                    "window exceeds the session retention horizon.",
        window="30d", consumer="AdminAnalytics", audience="admin",
        note="WAS the TOTAL registered user count relabelled, so DAU/MAU — the one "
             "number the pair exists to produce — was meaningless.",
        required_source="A durable per-user activity record (or a retained activity "
                        "event stream) spanning at least the reporting window.",
        prefer_unavailable=True, priority="P2", defect="F-14",
        ph39_resolution="DEPARTURE FROM THE PH3.8 INVENTORY. It prescribed 'distinct "
                        "user_id in db.sessions over a rolling 30 IST days'. That "
                        "query runs and returns a number, and the number is wrong: "
                        "db.sessions has a TTL index deleting a session one refresh "
                        "lifetime (7 days by default) after last use, so the rows a "
                        "30-day window needs have been removed by the DATABASE. The "
                        "result would be a 7-day count under a 30-day label, "
                        "undercounting more the longer ago a user churned — a "
                        "fabricated number replaced by a systematically wrong one. "
                        "`analytics.sources.active_users` checks the window against "
                        "the horizon and refuses it; raise JWT_REFRESH_TTL_SECONDS "
                        "past 30 days and the same call starts returning a value.",
    ),
    _spec(
        key="admin.retention_rate", label="Retention rate", surface="admin",
        endpoint="GET /api/admin/analytics/users", provenance=UNAVAILABLE,
        source="(no retained activity history)",
        calculation="Cohort retention; the history it needs does not exist.",
        window="all", consumer="AdminAnalytics", audience="admin",
        note="WAS the literal 78.5 — a constant that did not move when users left.",
        required_source="Cohort retention needs per-user activity history spanning "
                        "several weeks plus a durable 'first seen'. db.sessions is "
                        "reaped by its TTL index and no other collection records "
                        "activity.",
        backfill_required=True, prefer_unavailable=True, priority="P2", defect="F-14",
        ph39_resolution="REMOVED → UNAVAILABLE. Explicitly not back-fillable: an "
                        "activity event that was never written cannot be "
                        "reconstructed from users.created_at.",
    ),
    _spec(
        key="admin.churn_rate", label="Churn rate", surface="admin",
        endpoint="GET /api/admin/analytics/users", provenance=UNAVAILABLE,
        source="(no subscription records)",
        calculation="Cancellations / expiries over an active-subscription base; "
                    "neither term exists.",
        window="all", consumer="AdminAnalytics", audience="admin",
        note="WAS the literal 4.2, rendered in the loss colour so it read as a "
             "measured warning.",
        required_source="Subscription cancellations / expiries over an active-"
                        "subscription base. Requires the records admin.mrr needs.",
        prefer_unavailable=True, priority="P2", defect="F-14",
        ph39_resolution="REMOVED → UNAVAILABLE. Blocked on the same integration as "
                        "admin.mrr and should ship in that change, not before it.",
    ),
    _spec(
        key="admin.growth_rate", label="Signup growth rate", surface="admin",
        endpoint="GET /api/admin/analytics/users", provenance=DERIVED,
        source="db.users.created_at",
        calculation="(signups this 30-day IST window − signups in the window "
                    "immediately before it) / the latter × 100. The base window is "
                    "derived via `analytics.periods.preceding`, so both halves are "
                    "guaranteed the same span.",
        window="30d", consumer="AdminAnalytics", audience="admin",
        note="WAS the literal 12.8, also rendered as the delta badge on the Total "
             "Users card where a constant reads as measured growth. Growth from a "
             "ZERO base is UNAVAILABLE rather than +100% or +∞ — the first signup of "
             "a platform's life is not infinite growth.",
        defect="F-14",
        ph39_resolution="REPLACED with a real period-over-period computation. It "
                        "measures SIGNUP growth and is named accordingly; it is not "
                        "revenue growth and not active-user growth.",
    ),
    _spec(
        key="admin.conversion_rate", label="Conversion rate", surface="admin",
        endpoint="GET /api/admin/analytics/users", provenance=DERIVED,
        source="db.users.role",
        calculation="(pro + elite) / total × 100.", window="all",
        consumer="AdminAnalytics", audience="admin",
        note="Genuinely derived from real role counts — but it measures conversion to "
             "a GRANTED ROLE, not to a paid plan, for as long as roles are assigned "
             "without payment.",
    ),
    _spec(
        key="admin.feature_usage_pct", label="Feature adoption %", surface="admin",
        endpoint="GET /api/admin/analytics/features", provenance=UNAVAILABLE,
        source="(no feature-usage event stream)",
        calculation="Distinct users per feature ÷ active users. Neither term exists: "
                    "the counts available are total EVENTS, not distinct users.",
        window="all", consumer="AdminAnalytics", audience="admin",
        note="WAS a fixed descending list — 85, 72, 68, 55, 50, 45, 40, 25, 20, 15 — "
             "unrelated to the usage_count beside it, and seven of the ten counts "
             "were themselves the literal 0. The bar and the number next to it "
             "disagreed by construction.",
        required_source="A feature-usage event stream (feature, user_id, timestamp). "
                        "`observability.metrics` per-route counters are the cheapest "
                        "substitute but are process-scoped and count requests rather "
                        "than users, so they answer a different question.",
        backfill_required=True, prefer_unavailable=True, priority="P2", defect="F-15",
        ph39_resolution="REMOVED → UNAVAILABLE for the percentage. The seven uncounted "
                        "rows were DELETED rather than zeroed — 'the scanner has 0 "
                        "uses' is a measurement claim and nothing measures the "
                        "scanner. The three real counts (AI Chat, Trading, "
                        "Notifications) remain, named as event counts.",
    ),
    _spec(
        key="admin.ai_provider_latency", label="AI provider latency and failures",
        surface="admin", endpoint="GET /api/admin/ai/status", provenance=DERIVED,
        source="observability.metrics (ai_requests_total, ai_request_errors_total, "
               "ai_request_duration_seconds)",
        calculation="Per-provider request totals by outcome, failure counts by error "
                    "class, and a p95 latency read as the bucket bound at or below "
                    "which 95%% of observations fell.",
        window="all", consumer="AdminAI", audience="admin",
        note="WAS latency_ms = 1200 (Claude) / 900 (Gemini), failures = 0 and "
             "fallbacks = 0, all literals — `failures: 0` sitting beside a live "
             "failure counter, so an operator could not see an outage the platform "
             "was already measuring. `fallbacks` is now "
             "ai_requests_total{provider=\"simulated\"}: every one is a user who got "
             "a canned response because no real model answered.",
        defect="F-16",
        ph39_resolution="REWIRED to the PH3.7 instruments, with two honesty "
                        "constraints. (1) Counters are process-scoped and reset on "
                        "restart, so every field is named *_since_start and ships "
                        "with `scope`; nothing is labelled 'today'. (2) Latency is a "
                        "p95 BUCKET BOUND, never sum/count — a mean latency is the "
                        "number that hides an outage.",
    ),
    _spec(
        key="admin.ai_estimated_cost", label="AI estimated cost", surface="admin",
        endpoint="GET /api/admin/ai/status, GET /api/admin/ai/usage",
        provenance=UNAVAILABLE, source="(no token accounting)",
        calculation="Token counts priced per model; no token counts are recorded.",
        window="all", consumer="AdminAI", audience="admin",
        note="WAS messages ÷ 2 × ₹0.015 (Claude) or ₹0.007 (Gemini), and per-user "
             "messages × 0.011: a flat per-message rate standing in for per-token "
             "billing, in an ambiguous currency (rates read as USD, the UI rendered "
             "₹), split 50/50 between providers regardless of which served the "
             "request.",
        required_source="Token counts from provider responses, priced per model.",
        prefer_unavailable=True, priority="P2", defect="F-16",
        ph39_resolution="REMOVED → UNAVAILABLE on both endpoints. A cost figure is "
                        "exactly the kind of number that gets forwarded to finance, "
                        "so an invented one is worse than none.",
    ),
    _spec(
        key="admin.ai_top_users", label="Top AI users", surface="admin",
        endpoint="GET /api/admin/ai/usage", provenance=DERIVED,
        source="db.chat_messages",
        calculation="$group by user_id, count, sort desc, limit 10; joined to users.",
        window="all", consumer="AdminAI", audience="admin",
        note="The count is real. PH3.9 renamed it `request_count` → `message_count`, "
             "because it counts stored chat messages (both turns) rather than "
             "provider requests, and dropped the fabricated estimated_cost beside it.",
    ),
    _spec(
        key="admin.api_health", label="External integration health", surface="admin",
        endpoint="GET /api/admin/apis/health", provenance=DERIVED,
        source="observability.health probes + observability.metrics "
               "(provider_requests_total, provider_request_duration_seconds)",
        calculation="Per logical provider: request totals by outcome (ok/empty/error), "
                    "an error rate, an empty rate, and a p95 latency bucket bound. "
                    "Dependency probes are run live. `configured` is reported as its "
                    "own column.",
        window="all", consumer="AdminAPIs", audience="admin",
        note="WAS a hardcoded list in which `status` meant 'a credential is "
             "configured' and was never probed, latency_ms / requests_today / "
             "requests_month / failure_rate were literals, and overall_status was the "
             "constant 'healthy' — an operational dashboard that reported a healthy "
             "platform during a total provider outage.",
        defect="F-16",
        ph39_resolution="REWIRED, and THE ROW LIST CHANGED — a departure from the "
                        "PH3.8 inventory's 'rewire' framing. The old table named "
                        "VENDORS (Yahoo Finance, Alpha Vantage) with individual "
                        "latencies, and those numbers cannot be sourced honestly: the "
                        "Market Gateway's Source Manager picks an upstream per "
                        "request and that choice is deliberately invisible above the "
                        "gateway (MARKET_DATA_ARCHITECTURE.md), which is why "
                        "instruments.PROVIDERS is a closed vocabulary of LOGICAL "
                        "providers. Only market_data and news have instrumentation "
                        "call sites; the rest report `not_measured` rather than a "
                        "green badge. The Razorpay row was DELETED outright — it "
                        "reported `status: configured` with a 300ms latency for an "
                        "integration that does not exist anywhere in the codebase.",
    ),
    _spec(
        key="admin.system_resources", label="CPU / RAM / disk", surface="admin",
        endpoint="GET /api/admin/system/health", provenance=REAL, source="psutil",
        calculation="live process/host measurement.", window="today",
        consumer="AdminSystemHealth", audience="admin",
        note="Single-process only: on a multi-worker deployment this is one worker's "
             "view, not the service's.",
    ),
    _spec(
        key="admin.redis_status", label="Redis and scheduler status", surface="admin",
        endpoint="GET /api/admin/system/health", provenance=DERIVED,
        source="observability.health probes + apscheduler.running",
        calculation="Redis from its registered readiness probe, preserving the "
                    "three-way pass/fail/skip answer; the scheduler asked directly "
                    "whether it is running, plus its job count.",
        window="all", consumer="AdminSystemHealth", audience="admin",
        note="WAS the literal 'not_configured' for Redis — stale from before PH2.7 "
             "shipped `infrastructure.redis_client`, so a working Redis reported as "
             "absent — and the constant 'running' for the scheduler, which stayed "
             "'running' after the scheduler died. That is the worst failure mode a "
             "status field has: green exactly when it needs to be red.",
        defect="F-16",
        ph39_resolution="REWIRED. `skip` (not configured) is preserved rather than "
                        "collapsed into 'unhealthy': services/cache.py falls back to "
                        "an in-process dict, so an unconfigured Redis is a valid "
                        "deployment, not a fault — and not a green light either. "
                        "`overall_status` is now derived from failing CRITICAL "
                        "dependencies instead of being the constant 'healthy'.",
    ),
    _spec(
        key="admin.dashboard_health_badges", label="Server / API health badges",
        surface="admin", endpoint="GET /api/admin/dashboard", provenance=DERIVED,
        source="observability.health probes",
        calculation="db_health from the live mongodb probe; server_health is the "
                    "constant 'serving'; `degraded_dependencies` lists failing "
                    "critical probes.",
        window="all", consumer="AdminDashboard", audience="admin",
        note="WAS `api_health: 'healthy'` and `server_health: 'healthy'`, two "
             "literals that checked nothing. `server_health` is now 'serving' — the "
             "only thing a health field served BY a process can honestly assert "
             "about that process is that it answered this request. The former "
             "`api_health` literal is replaced by real dependency results.",
        defect="F-16",
        ph39_resolution="REWIRED to observability.health.",
    ),
]

# --------------------------------------------------------------------------- #
# UNAVAILABLE — metrics the product implies, or a trading platform is expected
# to carry, that cannot be computed from anything currently persisted. Listed so
# PH3.9 does not "solve" them by inventing a formula.
# --------------------------------------------------------------------------- #
_UNAVAILABLE = [
    _spec(
        key="portfolio.time_weighted_return", label="Time-weighted return",
        surface="portfolio", endpoint="(none)", provenance=UNAVAILABLE,
        source="(no external-flow records)",
        calculation="Requires per-day deposits and withdrawals to neutralise the "
                    "effect of cash flows. `portfolio_snapshots` stores invested and "
                    "current_value but nothing distinguishes 'invested more' from "
                    "'gained'.",
        window="all", consumer="Portfolio",
        note="The metric a performance chart is normally expected to show, and the "
             "one this product cannot honestly produce. `portfolio.pct_return` is "
             "shipped in its place with `flow_adjusted: false` and a caveat when a "
             "cost-basis change is detected — a detector, not a correction. Inventing "
             "a TWR from the data that exists would be the single most damaging "
             "fabrication in the product, because a trader would compare it to a "
             "benchmark.",
        required_source="A cash-flow ledger: dated external contributions and "
                        "withdrawals per user.",
        backfill_required=True, prefer_unavailable=True, priority="P3", defect="F-8",
    ),
    _spec(
        key="trading.profit_factor", label="Profit factor", surface="trading",
        endpoint="(none)", provenance=UNAVAILABLE, source="db.trades",
        calculation="gross profit / gross loss. Computable in principle from closed "
                    "trades, but every closed-trade P&L in this product is GROSS: no "
                    "brokerage, STT, exchange charge, GST, stamp duty or SEBI fee is "
                    "recorded anywhere.",
        window="all", consumer="(not rendered)",
        note="Deliberately NOT implemented in PH3.8. On Indian intraday equity, "
             "charges routinely exceed the edge on a small trade — a profit factor "
             "computed gross is systematically optimistic, and a trader would act on "
             "it. The same objection applies to any expectancy or Sharpe figure over "
             "this data.",
        required_source="Per-fill charges from the broker contract note.",
        prefer_unavailable=True, priority="P3", defect="F-17",
    ),
    _spec(
        key="trading.net_pnl_after_charges", label="Net P&L after charges",
        surface="trading", endpoint="(none)", provenance=UNAVAILABLE,
        source="(no charges recorded)",
        calculation="realised P&L minus brokerage, STT, transaction charges, GST, "
                    "SEBI turnover fee and stamp duty.",
        window="all", consumer="(not rendered)",
        note="Every P&L figure the product displays is gross. This is stated in "
             "ANALYTICS.md rather than silently assumed.",
        required_source="Broker contract-note charges per fill.",
        prefer_unavailable=True, priority="P2", defect="F-17",
    ),
    _spec(
        key="trading.avg_win_avg_loss", label="Average win / average loss",
        surface="trading", endpoint="(none)", provenance=UNAVAILABLE, source="db.trades",
        calculation="mean pnl over winners and over losers.",
        window="all", consumer="(not rendered)",
        note="Computable and honest at gross, but not currently surfaced anywhere. "
             "Listed so it is added deliberately with its gross caveat rather than "
             "quietly.",
        required_source="Already available; needs a surface and the gross caveat.",
        priority="P3",
    ),
    _spec(
        key="admin.arpu", label="ARPU", surface="admin", endpoint="(none)",
        provenance=UNAVAILABLE, source="(no payment records)",
        calculation="revenue / active users.", window="30d", consumer="(not rendered)",
        audience="admin",
        note="Blocked on the same missing payment records as every other revenue "
             "metric.",
        required_source="Captured payments.", prefer_unavailable=True, priority="P3",
        defect="F-13",
    ),
]

#: The complete inventory.
REGISTRY = tuple(_TRADING + _JOURNAL + _PAPER + _PORTFOLIO + _RESEARCH + _ADMIN + _UNAVAILABLE)


def by_provenance(provenance: str) -> tuple:
    if provenance not in PROVENANCE:
        raise ValueError(f"{provenance!r} is not one of {PROVENANCE}")
    return tuple(s for s in REGISTRY if s.provenance == provenance)


def by_surface(surface: str) -> tuple:
    return tuple(s for s in REGISTRY if s.surface == surface)


def get(key: str) -> Optional[MetricSpec]:
    return next((s for s in REGISTRY if s.key == key), None)


def endpoints() -> tuple:
    """Every distinct API route in the inventory, excluding unrendered metrics."""
    seen = []
    for spec in REGISTRY:
        for endpoint in spec.endpoint.split(","):
            endpoint = endpoint.strip()
            if endpoint and endpoint != "(none)" and endpoint not in seen:
                seen.append(endpoint)
    return tuple(seen)


def ph39_inventory() -> tuple:
    """The unanswerable-metric inventory, highest priority first.

    Before PH3.9 this was the *mock-removal specification*: every MOCK plus
    every UNAVAILABLE entry, handed forward as work. After PH3.9 there are no
    MOCK entries left, so what it returns is the standing list of metrics the
    product cannot honestly compute — each with the production data that would
    make it answerable. That is the more useful long-lived shape, and it is why
    the function is kept rather than deleted with the mocks: the next person to
    ask "why does the admin portal say revenue is unavailable?" gets an answer
    with a required source attached.
    """
    order = {"P1": 0, "P2": 1, "P3": 2, "": 3}
    open_items = [s for s in REGISTRY if s.provenance in (MOCK, UNAVAILABLE)]
    return tuple(sorted(open_items, key=lambda s: (order.get(s.priority, 3), s.key)))


def ph39_resolutions() -> tuple:
    """Every metric PH3.9 acted on, with what it did and why.

    The record of the removal sprint, in the same file as the classification it
    changed — so "which mocks were removed, and what replaced each" is answerable
    from code rather than from a changelog entry that will drift.
    """
    return tuple(s for s in REGISTRY if s.ph39_resolution)


def summary() -> dict:
    return {p: len(by_provenance(p)) for p in PROVENANCE}
