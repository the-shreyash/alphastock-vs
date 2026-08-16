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
    #: MOCK entries only — the PH3.9 replacement specification.
    required_source: str = ""     # the production data that would make it real
    backfill_required: bool = False
    prefer_unavailable: bool = False   # PH3.9 should show UNAVAILABLE, not invent a value
    priority: str = ""            # P1 | P2 | P3
    defect: str = ""              # PH3.8 finding id, when one applies


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
        surface="research", endpoint="POST /api/backtest", provenance=MOCK,
        source="random.randint / random.uniform",
        calculation="20 invented trades with a win count drawn from randint(10, 16) — "
                    "so the win rate is always 50–80%, always flattering — over an "
                    "invented price series with invented 2025 dates.",
        window="all", consumer="Backtesting",
        note="Returned on ANY yfinance failure (import error, network blip, rate "
             "limit, delisted symbol), not only when the library is absent. The "
             "response sets data_source='synthetic' and the UI shows a note, but the "
             "metric cards render the fabricated Sharpe, drawdown and win rate with "
             "identical styling to a real run. The seed is `hash(str)`, which is "
             "PYTHONHASHSEED-salted, so the same backtest returns a different win "
             "rate on every process — measured at 80% / 60% / 80% across three runs "
             "of identical input.",
        required_source="Real historical OHLCV. Either a hard dependency on the "
                        "market-data gateway's historical path, or an explicit "
                        "failure.",
        prefer_unavailable=True, priority="P1", defect="F-11",
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
        key="admin.ai_requests_today", label="AI requests today", surface="admin",
        endpoint="GET /api/admin/dashboard", provenance=DERIVED,
        source="db.chat_messages.created_at",
        calculation="count of chat messages created in the IST day.", window="today",
        consumer="AdminDashboard", audience="admin",
        note="Counts stored messages, which includes both the user turn and the "
             "assistant turn — it is roughly 2× the provider requests. "
             "`observability.instruments` now counts real provider calls; PH3.9 "
             "should read that instead.",
        defect="F-12",
    ),
    _spec(
        key="admin.revenue_today", label="Revenue today", surface="admin",
        endpoint="GET /api/admin/dashboard", provenance=MOCK,
        source="db.payments count × 499",
        calculation="total payment documents × ₹499. Not a sum of amounts, not "
                    "date-filtered, and the price is a literal.",
        window="today", consumer="AdminDashboard", audience="admin",
        note="Renders as ₹0 today only because `db.payments` is empty — the platform "
             "has no payment integration and nothing writes to that collection. The "
             "instant one record lands, this reports ₹499 of 'today's revenue' "
             "regardless of amount, currency, status or date.",
        required_source="Verified payment records with amount, currency, status and "
                        "captured_at, written by the payment provider webhook.",
        prefer_unavailable=True, priority="P1", defect="F-13",
    ),
    _spec(
        key="admin.mrr", label="MRR", surface="admin",
        endpoint="GET /api/admin/dashboard, GET /api/admin/payments/stats",
        provenance=MOCK, source="db.users.role × hardcoded prices",
        calculation="pro/premium count × ₹499 + elite count × ₹999.",
        window="all", consumer="AdminDashboard, AdminPayments", audience="admin",
        note="Revenue inferred from role assignment. Roles are granted by an admin "
             "through POST /api/admin/users/{id}/grant-plan with no payment involved, "
             "so every comped account, every internal account and every beta tester "
             "is counted as paying. Prices are literals in the route, not the "
             "pricing model. Lifetime plans contribute ₹0 monthly, which is right by "
             "accident rather than by design.",
        required_source="Active subscription records (plan, price, currency, billing "
                        "interval, status, current_period_end) reconciled against "
                        "captured payments.",
        prefer_unavailable=True, priority="P1", defect="F-13",
    ),
    _spec(
        key="admin.arr", label="ARR", surface="admin",
        endpoint="GET /api/admin/dashboard, GET /api/admin/payments/stats",
        provenance=MOCK, source="admin.mrr × 12",
        calculation="MRR × 12.", window="all", consumer="AdminDashboard, AdminPayments",
        audience="admin", note="Inherits every defect of admin.mrr and multiplies it.",
        required_source="As admin.mrr.", prefer_unavailable=True, priority="P1",
        defect="F-13",
    ),
    _spec(
        key="admin.revenue_series", label="Revenue trend (30d)", surface="admin",
        endpoint="GET /api/admin/analytics/revenue", provenance=MOCK,
        source="a for-loop",
        calculation="revenue = 2500 + (i × 150) + (500 if i %% 7 == 0 else 0); "
                    "subscriptions = 3 + (i %% 5). No database access of any kind.",
        window="30d", consumer="AdminAnalytics", audience="admin",
        note="The most misleading artefact in the product: a smooth, always-up-and-to-"
             "the-right revenue chart, rendered by Recharts with no visual "
             "distinction from a real series, on an installation that has never "
             "processed a payment.",
        required_source="Captured payments aggregated by IST day.",
        backfill_required=True, prefer_unavailable=True, priority="P1", defect="F-13",
    ),
    _spec(
        key="admin.revenue_window_totals", label="Revenue today/week/month/year",
        surface="admin", endpoint="GET /api/admin/payments/stats", provenance=MOCK,
        source="literals and MRR",
        calculation="revenue_today = 0, revenue_week = 0, revenue_month = MRR, "
                    "revenue_year = ARR — all literals; no date filtering exists.",
        window="all", consumer="AdminPayments", audience="admin",
        note="revenue_today and revenue_week are hardcoded zeros that a reader cannot "
             "distinguish from a genuine no-revenue day.",
        required_source="Captured payments, summed per window.",
        prefer_unavailable=True, priority="P1", defect="F-13",
    ),
    _spec(
        key="admin.payment_states", label="Pending / refunded / failed payments",
        surface="admin", endpoint="GET /api/admin/payments/stats", provenance=MOCK,
        source="literals",
        calculation="pending_payments = 0, refunds = 0, failed_payments = 0.",
        window="all", consumer="AdminPayments", audience="admin",
        note="Compounded by PH3.5's D-4: POST /api/admin/payments/{id}/refund is a "
             "stub that returns success and writes a `payment.refunded` audit record "
             "for a refund that never happened. Refunds therefore read as zero while "
             "the audit log says otherwise.",
        required_source="Payment records with a status field maintained by provider "
                        "webhooks.",
        prefer_unavailable=True, priority="P1", defect="F-13",
    ),
    _spec(
        key="admin.dau", label="Daily active users", surface="admin",
        endpoint="GET /api/admin/analytics/users", provenance=MOCK,
        source="db.users.created_at",
        calculation="today's SIGNUPS, relabelled as DAU.",
        window="today", consumer="AdminAnalytics", audience="admin",
        note="Signups and active users are different populations; on a mature "
             "product they differ by orders of magnitude. The source comment says "
             "'Simplified; real system tracks active sessions' — and `db.sessions` "
             "(PH1.6) now exists and does exactly that.",
        required_source="db.sessions — distinct user_id with activity in the IST day. "
                        "The data already exists; only the query is missing.",
        prefer_unavailable=False, priority="P2", defect="F-14",
    ),
    _spec(
        key="admin.mau", label="Monthly active users", surface="admin",
        endpoint="GET /api/admin/analytics/users", provenance=MOCK,
        source="db.users",
        calculation="TOTAL registered users, relabelled as MAU.",
        window="30d", consumer="AdminAnalytics", audience="admin",
        note="Equals total_users exactly, so the ratio DAU/MAU — the one number "
             "these two exist to produce — is meaningless.",
        required_source="db.sessions — distinct user_id over a rolling 30 IST days.",
        priority="P2", defect="F-14",
    ),
    _spec(
        key="admin.retention_rate", label="Retention rate", surface="admin",
        endpoint="GET /api/admin/analytics/users", provenance=MOCK, source="literal 78.5",
        calculation="the literal 78.5.", window="all", consumer="AdminAnalytics",
        audience="admin",
        note="A constant. It does not move when users leave.",
        required_source="Cohort retention over db.sessions: of users first seen in "
                        "cohort week N, the fraction with activity in week N+k.",
        backfill_required=True, prefer_unavailable=True, priority="P2", defect="F-14",
    ),
    _spec(
        key="admin.churn_rate", label="Churn rate", surface="admin",
        endpoint="GET /api/admin/analytics/users", provenance=MOCK, source="literal 4.2",
        calculation="the literal 4.2.", window="all", consumer="AdminAnalytics",
        audience="admin",
        note="Rendered in the loss colour, which reads as a measured warning.",
        required_source="Subscription cancellations / expiries per period over an "
                        "active-subscription base. Requires the subscription records "
                        "admin.mrr also needs.",
        prefer_unavailable=True, priority="P2", defect="F-14",
    ),
    _spec(
        key="admin.growth_rate", label="Growth rate", surface="admin",
        endpoint="GET /api/admin/analytics/users", provenance=MOCK, source="literal 12.8",
        calculation="the literal 12.8.", window="all", consumer="AdminAnalytics",
        audience="admin",
        note="Also rendered as the delta badge on the Total Users card, where a "
             "constant reads as measured period-over-period growth.",
        required_source="Signups this period vs the previous period over db.users."
                        "created_at — computable today from data that already exists.",
        prefer_unavailable=False, priority="P2", defect="F-14",
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
        key="admin.feature_usage_pct", label="Feature usage %", surface="admin",
        endpoint="GET /api/admin/analytics/features", provenance=MOCK,
        source="literals",
        calculation="a fixed descending list — 85, 72, 68, 55, 50, 45, 40, 25, 20, 15 — "
                    "unrelated to the usage_count beside it.",
        window="all", consumer="AdminAnalytics", audience="admin",
        note="Six of the ten features also report usage_count = 0 as a literal (the "
             "scanner, morning report, portfolio, news, SIP advisor, paper trading and "
             "backtesting rows are never counted), so the bar and the number next to "
             "it disagree by construction. Three rows — AI Chat, Trading, "
             "Notifications — do carry a real count.",
        required_source="A feature-usage event stream. No such collection exists; "
                        "`observability.metrics` counts requests per route template "
                        "in-process and is the cheapest honest substitute.",
        backfill_required=True, prefer_unavailable=True, priority="P2", defect="F-15",
    ),
    _spec(
        key="admin.ai_provider_latency", label="AI provider latency", surface="admin",
        endpoint="GET /api/admin/ai/status", provenance=MOCK, source="literals",
        calculation="latency_ms = 1200 for Claude, 900 for Gemini; failures = 0; "
                    "fallbacks = 0; model names are hardcoded strings.",
        window="all", consumer="AdminAI", audience="admin",
        note="PH3.7 shipped real instruments for exactly these numbers — "
             "`ai_request_duration_seconds` and `ai_requests_total{provider,outcome}` "
             "— and this page does not read them. `failures = 0` next to a live "
             "failure counter is the worst case: an operator watching this page "
             "cannot see an outage the platform is already recording.",
        required_source="observability.metrics registry (PH3.7).",
        prefer_unavailable=False, priority="P1", defect="F-16",
    ),
    _spec(
        key="admin.ai_estimated_cost", label="AI estimated cost", surface="admin",
        endpoint="GET /api/admin/ai/status, GET /api/admin/ai/usage", provenance=MOCK,
        source="message count × literal rate",
        calculation="messages ÷ 2 × ₹0.015 (Claude) or ₹0.007 (Gemini); per-user cost "
                    "is messages × 0.011.",
        window="all", consumer="AdminAI", audience="admin",
        note="A per-message flat rate, when provider billing is per TOKEN and varies "
             "by model and by cache hit. The currency is ambiguous — the rates look "
             "like USD, the UI renders ₹. Split 50/50 between providers regardless of "
             "which one actually served the request.",
        required_source="Token counts from provider responses, priced per model.",
        prefer_unavailable=True, priority="P2", defect="F-16",
    ),
    _spec(
        key="admin.ai_top_users", label="Top AI users", surface="admin",
        endpoint="GET /api/admin/ai/usage", provenance=DERIVED,
        source="db.chat_messages",
        calculation="$group by user_id, count, sort desc, limit 10; joined to users.",
        window="all", consumer="AdminAI", audience="admin",
        note="The request_count is real. Only the estimated_cost beside it is not.",
    ),
    _spec(
        key="admin.api_health", label="External API health", surface="admin",
        endpoint="GET /api/admin/apis/health", provenance=MOCK, source="literals",
        calculation="a hardcoded list. status is 'online' whenever a KEY IS "
                    "CONFIGURED — never probed. latency_ms, requests_today, "
                    "requests_month and failure_rate are literals; overall_status is "
                    "the constant 'healthy'.",
        window="all", consumer="AdminAPIs", audience="admin",
        note="An operational dashboard that reports healthy during a total provider "
             "outage. PH3.7's `/api/health/detailed` performs real dependency probes "
             "and `provider_requests_total{provider,outcome}` counts real outcomes.",
        required_source="observability.health probes + observability.metrics (PH3.7).",
        prefer_unavailable=False, priority="P1", defect="F-16",
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
        key="admin.redis_status", label="Redis status", surface="admin",
        endpoint="GET /api/admin/system/health", provenance=MOCK,
        source="the literal 'not_configured'",
        calculation="a hardcoded string.", window="all",
        consumer="AdminSystemHealth", audience="admin",
        note="Stale since PH2.7 shipped `infrastructure.redis_client` and PH3.7 "
             "registered a real Redis readiness probe. The scheduler status beside it "
             "is the hardcoded string 'running' and stays 'running' after the "
             "scheduler dies.",
        required_source="observability.health dependency probes (PH2.5/PH3.7).",
        prefer_unavailable=False, priority="P1", defect="F-16",
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
    """The mock-removal specification handed to PH3.9, highest priority first."""
    order = {"P1": 0, "P2": 1, "P3": 2, "": 3}
    mocks = [s for s in REGISTRY if s.provenance in (MOCK, UNAVAILABLE)]
    return tuple(sorted(mocks, key=lambda s: (order.get(s.priority, 3), s.key)))


def summary() -> dict:
    return {p: len(by_provenance(p)) for p in PROVENANCE}
