from pydantic import BaseModel, Field, ConfigDict, BeforeValidator, model_validator
from pydantic_core import PydanticCustomError
from typing import Optional, List, Annotated
from datetime import datetime, timezone
from bson import ObjectId

from security.passwords import normalize_password, validate_new_password


def _object_id_to_str(v):
    if isinstance(v, ObjectId):
        return str(v)
    return str(v) if v else v

PyObjectId = Annotated[str, BeforeValidator(_object_id_to_str)]


class BaseDocument(BaseModel):
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)
    id: Optional[PyObjectId] = Field(default=None, alias="_id")

    def to_mongo(self):
        d = self.model_dump(by_alias=True, exclude_none=True)
        d.pop("_id", None)
        return d

    @classmethod
    def from_mongo(cls, doc):
        if doc is None:
            return None
        return cls(**doc)


# --- Auth Models ---
class UserCreate(BaseModel):
    name: str
    email: str
    password: str

    @model_validator(mode="after")
    def _enforce_password_policy(self):
        """Production password policy (PH1.5), enforced at the model layer so a
        weak password is rejected with 422 before it ever reaches hashing.
        Cross-field by design: the policy rejects passwords derived from the
        user's own email or name. The normalized (whitespace-stripped) password
        replaces the raw value so validation and hashing can never disagree.
        Applies to NEW registrations only — UserLogin is deliberately
        unvalidated so existing accounts keep working."""
        self.password = normalize_password(self.password)
        violations = validate_new_password(self.password, email=self.email, name=self.name)
        if violations:
            raise PydanticCustomError("password_policy", " ".join(violations))
        return self

class UserLogin(BaseModel):
    email: str
    password: str


# --- Identity Recovery Models (PH1.8) ---
# Password-policy validation for the NEW password in the reset/change flows is
# NOT done here: it is identity-aware (rejects passwords derived from the user's
# own email/name) and the user's identity is only known after the token/session
# is resolved server-side. The recovery endpoints run validate_new_password with
# the resolved account, mirroring how UserCreate validates at registration.
class VerifyEmailRequest(BaseModel):
    """Redeem an email-verification token (from the emailed link)."""
    token: str


class ForgotPasswordRequest(BaseModel):
    """Start password recovery for an email. The response is always generic —
    it never reveals whether the address is registered."""
    email: str


class ResetPasswordRequest(BaseModel):
    """Complete password recovery with a reset token + a new password."""
    token: str
    new_password: str


class ChangePasswordRequest(BaseModel):
    """Change the password of the authenticated user. Requires the current
    password (re-authentication) and revokes every session on success."""
    current_password: str
    new_password: str

class UserResponse(BaseDocument):
    name: str
    email: str
    role: str = "user"
    capital: float = 100000
    risk_level: str = "moderate"
    max_daily_loss: float = 5000
    max_trades_per_day: int = 3
    telegram_chat_id: Optional[str] = None
    # Trading platform chosen by the user in Settings. There is NO default —
    # one-click / quick trading stays disabled until the user explicitly
    # selects a connected broker (Sprint 9.1).
    preferred_broker: Optional[str] = None
    created_at: Optional[str] = None

class NotificationPrefs(BaseModel):
    push: bool = True
    email: bool = True
    morning_report: bool = True
    trade_alerts: bool = True
    exit_reminder: bool = True
    portfolio_alerts: bool = True
    email_alerts: bool = True
    telegram_alerts: bool = True


# --- Trade Models ---
class TrailingStopConfig(BaseModel):
    """Trailing stop config (Sprint 9). The engine ratchets the stop toward
    the best traded price by `value` percent/points — it never loosens."""
    enabled: bool = False
    type: str = Field(default="percent", pattern="^(percent|points)$")
    value: float = Field(default=0, ge=0)


# --------------------------------------------------------------------------- #
# Canonical trade-entry constraints (PH3.12R / B-1)                             #
# --------------------------------------------------------------------------- #
# WHY THESE ARE NAMED TYPES AND NOT INLINE `Field(...)` CALLS
# -----------------------------------------------------------
# PH3.12 found that `/api/paper/trade` accepted `quantity: -1000` and answered
# 200, crediting the caller's paper balance by ₹10,00,000 — while `/api/trades`
# rejected the identical payload with 422. Both endpoints create a trade in the
# same collection from the same six numbers. They disagreed because the
# constraints were written out twice: once here on `TradeCreate`, and once (as
# bare, unconstrained annotations) on a `PaperTradeCreate` declared 5,000 lines
# away inside `server.py`.
#
# The fix is not "copy the Field() calls across". Copied constraints drift again
# the moment a third trade-entry surface is added, and the drift is invisible
# because nothing links the two declarations. So the contract now exists ONCE,
# as these aliases, and every trade-entry model is spelled in terms of them.
# Tightening a bound here tightens it everywhere by construction.
#
# `allow_inf_nan=False` is load-bearing and not obvious: Python's `json.loads`
# — which is what Starlette parses request bodies with — accepts the
# non-standard literals `Infinity`, `-Infinity` and `NaN`. A plain `gt=0` float
# **admits `Infinity`** (`inf > 0` is True; only `NaN` fails the comparison), so
# `entry_price: Infinity` would have passed every bound above and written an
# infinite `total_cost` into the trade journal, poisoning every downstream P&L
# and performance aggregate with `inf`/`nan`. Verified empirically, not assumed.

#: Trade direction. The only two sides the engine, the journal and the P&L
#: calculations understand — anything else silently takes the SELL branch of
#: every `type == "BUY"` check in the codebase.
TradeSide = Annotated[str, Field(pattern="^(BUY|SELL)$")]

#: Share count. Strictly positive (a negative quantity inverts the sign of every
#: cost and P&L term) and capped at the same 100,000 ceiling `TradeCreate` has
#: always enforced, which bounds the notional a single request can express.
TradeQuantity = Annotated[int, Field(gt=0, le=100000)]

#: Any price that must be a real, tradeable level: entry, stop-loss, target.
TradePrice = Annotated[float, Field(gt=0, allow_inf_nan=False)]

#: A price whose absence is encoded as 0 rather than null (`target2` on the
#: paper-trading form). Zero means "not set"; negative is still meaningless.
OptionalTradePrice = Annotated[float, Field(ge=0, allow_inf_nan=False)]

#: An NSE/BSE ticker. Bounded and character-restricted so a symbol cannot carry
#: a payload into the trade journal, the activity log or the analytics
#: group-by keys. `&` and `-` are permitted because real listings use them
#: (`M&M`, `BAJAJ-AUTO`); lowercase is permitted because the service upper-cases
#: on the way in. Anchored `^…$` — Pydantic's `pattern` is an unanchored match.
TradeSymbol = Annotated[str, Field(min_length=1, max_length=32,
                                   pattern=r"^[A-Za-z0-9][A-Za-z0-9&.\-]{0,31}$")]

#: Free-text fields that are persisted. Unbounded strings in a stored document
#: are a storage-amplification vector, not a validation nicety.
TradeFreeText = Annotated[str, Field(max_length=2000)]
TradeShortText = Annotated[str, Field(max_length=120)]


class TradeCreate(BaseModel):
    symbol: str
    stock_name: str
    type: TradeSide = "BUY"
    entry_price: TradePrice
    quantity: TradeQuantity
    stop_loss: TradePrice
    target1: TradePrice
    target2: Optional[float] = None
    target3: Optional[float] = None
    trailing_stop: Optional[TrailingStopConfig] = None
    notes: Optional[str] = None
    setup_type: Optional[str] = None
    is_paper: bool = False
    # Broker execution (Sprint 9): when `broker` is set the entry order is
    # placed LIVE via the Broker Engine before the trade record is created.
    broker: Optional[str] = None
    product: Optional[str] = None          # CNC/MIS/NRML — defaults per broker
    exchange: str = "NSE"
    order_type: str = Field(default="MARKET", pattern="^(MARKET|LIMIT)$")
    # Explicit consent for the engine to place live EXIT orders on SL/target
    # hits for this trade (BROKER_INTEGRATION.md compliance). Default off.
    auto_exit: bool = False
    # Acknowledge risk warnings (violations always block regardless).
    override_warnings: bool = False


class PaperTradeCreate(BaseModel):
    """Entry payload for `POST /api/paper/trade` (PH3.12R / B-1).

    LIVES HERE, NEXT TO `TradeCreate`, ON PURPOSE
    ---------------------------------------------
    This model used to be declared inline in `server.py` beside its route
    handler. That is why B-1 existed: nobody editing `TradeCreate` could see
    that a second model described the same domain, so the two never got
    tightened together. Keeping them adjacent — and spelled in the same alias
    vocabulary — makes the divergence visible at a glance and impossible to
    reintroduce without deleting a shared type.

    Every constraint `TradeCreate` enforces is enforced here. The two models
    are NOT merged into one because their optional surfaces genuinely differ:
    a paper trade has no broker, no exchange, no order type, no live-exit
    consent and no risk-warning override — fields that must not exist on an
    endpoint that can never place an order.
    """
    symbol: TradeSymbol
    stock_name: TradeShortText = ""
    quantity: TradeQuantity
    entry_price: TradePrice
    type: TradeSide = "BUY"
    stop_loss: TradePrice
    target1: TradePrice
    # 0 is the caller's "no second target" sentinel; `execute_paper_trade`
    # stores `target2 or target1`. Negative is rejected, unlike before.
    target2: OptionalTradePrice = 0.0
    setup_type: TradeShortText = "MOMENTUM"
    notes: TradeFreeText = ""

    # Reject unknown keys outright. A paper-trading payload carrying `broker`,
    # `auto_exit` or `is_paper: false` is either a client bug or an attempt to
    # reach the live-execution surface through the simulated one; silently
    # dropping it hides both. Real fields are added deliberately, not by a
    # caller guessing at names.
    model_config = ConfigDict(extra="forbid")


class TradeModify(BaseModel):
    """Editable fields of an OPEN trade (validated against entry side)."""
    stop_loss: Optional[float] = Field(default=None, gt=0)
    target1: Optional[float] = Field(default=None, gt=0)
    target2: Optional[float] = None       # 0/None clears the target
    target3: Optional[float] = None
    trailing_stop: Optional[TrailingStopConfig] = None
    notes: Optional[str] = None


class TradeExitRequest(BaseModel):
    """Exit an open trade — fully or partially, manually or at market via the
    connected broker (`at_market` requires the trade to be broker-linked)."""
    exit_price: Optional[float] = Field(default=None, gt=0)
    quantity: Optional[int] = Field(default=None, gt=0)   # None = all remaining
    at_market: bool = False

class TradeResponse(BaseDocument):
    user_id: Optional[str] = None
    symbol: str
    stock_name: str
    type: str = "BUY"
    entry_price: float
    exit_price: Optional[float] = None
    quantity: int
    quantity_open: Optional[int] = None
    realized_pnl: Optional[float] = None
    stop_loss: float
    initial_stop_loss: Optional[float] = None
    target1: float
    target2: Optional[float] = None
    target3: Optional[float] = None
    trailing_stop: Optional[TrailingStopConfig] = None
    best_price: Optional[float] = None
    targets_hit: Optional[List[dict]] = None
    events: Optional[List[dict]] = None
    broker: Optional[str] = None
    broker_order_id: Optional[str] = None
    auto_exit: bool = False
    status: str = "OPEN"
    pnl: Optional[float] = None
    pnl_percent: Optional[float] = None
    entry_time: Optional[str] = None
    exit_time: Optional[str] = None
    ai_confidence: Optional[float] = None
    ai_explanation: Optional[str] = None
    notes: Optional[str] = None
    setup_type: Optional[str] = None
    is_paper: bool = False


# --- Watchlist Models ---
class WatchlistAdd(BaseModel):
    symbol: str
    note: Optional[str] = None


# --- Chat Models ---
class ChatMessage(BaseModel):
    message: str
    session_id: Optional[str] = None
    # Client-generated correlation id (Sprint R7). Lets the live AI step timeline
    # (ai.run.* / ai.step events over WebSocket) be matched to this request.
    run_id: Optional[str] = None

class ChatResponse(BaseModel):
    response: str
    claude_analysis: Optional[str] = None
    gemini_analysis: Optional[str] = None
    session_id: str


# --- Analysis Models ---
class StockAnalysisRequest(BaseModel):
    symbol: str
    name: Optional[str] = None
    force: bool = False  # bypass the per-day AI explain cache

class SIPRequest(BaseModel):
    amount: float
    goal: str
    years: int
    risk: str = "moderate"
    age: int = 30
    tax_bracket: str = "30%"


# --- Investment Advisor Models ---
# The AI Investment Advisor recommends stocks across multiple horizons
# (long / medium / short / swing / intraday). Every recommendation is built
# from REAL market data (services.real_market) and is fully self-explaining —
# see .claude/project.md: each AI recommendation must justify why / confidence /
# technical / fundamental / risk / reward / news / sector.

class AdvisorRequest(BaseModel):
    # One of: "long" | "medium" | "short" | "swing" | "intraday".
    horizon: str = "swing"
    # Optional: "conservative" | "moderate" | "aggressive" — biases selection.
    risk_appetite: Optional[str] = None
    # Optional sector filter (e.g. ["Banking", "IT"]) — matched case-insensitively.
    sectors: Optional[List[str]] = None
    # Optional investable capital in INR (used only for context in the narrative).
    capital: Optional[float] = None


class AdvisorEntryZone(BaseModel):
    low: float = 0.0
    high: float = 0.0


class AdvisorRecommendation(BaseModel):
    """Shape of a single stock recommendation returned by the advisor.

    Constructed server-side to guarantee a stable, documented contract for the
    frontend even when the AI narrative or live data degrades gracefully."""
    symbol: str
    name: str
    sector: str = ""
    confidence: int = 0            # 0-100 technical conviction
    risk: str = "Medium"          # "Low" | "Medium" | "High"
    expected_return_pct: float = 0.0
    holding_period: str = ""      # human string tuned to the horizon
    entry_zone: AdvisorEntryZone = Field(default_factory=AdvisorEntryZone)
    stop_loss: float = 0.0
    targets: List[float] = Field(default_factory=list)
    technical_reasons: List[str] = Field(default_factory=list)
    fundamental_reasons: List[str] = Field(default_factory=list)
    news_impact: str = ""
    sector_strength: str = ""
    ai_summary: str = ""
    # Supplementary context (real values that drove the recommendation)
    price: float = 0.0
    horizon: str = ""
    rsi: Optional[float] = None
    volume_ratio: Optional[float] = None
    pattern: Optional[str] = None


# --- AI Workspace Models ---
# Powers the AI Workspace (Sprint 6). Memory + conversation + learning +
# trade review flows are served by the /api/ai router and follow the
# centralized Prompt Library (services/prompt_library.py).

class AIMemoryUpdate(BaseModel):
    """Editable slice of a user's AI memory (see services/ai_memory.py)."""
    risk_preference: Optional[str] = None
    goals: Optional[str] = None
    preferred_sectors: Optional[List[str]] = None
    favorite_companies: Optional[List[str]] = None
    experience_level: Optional[str] = None
    preferred_language: Optional[str] = None
    notes: Optional[str] = None


class LearnRequest(BaseModel):
    """Ask the Learning Mentor to teach a concept."""
    topic: str
    level: str = "beginner"  # beginner | intermediate | advanced


class TradeReviewRequest(BaseModel):
    """Review a closed trade. Either reference an existing trade by id, or pass
    a raw trade dict for ad-hoc review (e.g. a paper/manual trade)."""
    trade_id: Optional[str] = None
    trade: Optional[dict] = None
    # Client-generated correlation id for the live AI step timeline (Sprint R7).
    run_id: Optional[str] = None


class PortfolioReviewRequest(BaseModel):
    """Optional body for the Portfolio AI review — carries only the live-run
    correlation id (Sprint R7). Requests without a body remain valid."""
    run_id: Optional[str] = None


# --- Settings Models ---
class UserSettingsUpdate(BaseModel):
    name: Optional[str] = None
    capital: Optional[float] = None
    risk_level: Optional[str] = None
    max_daily_loss: Optional[float] = None
    max_trades_per_day: Optional[int] = None
    telegram_chat_id: Optional[str] = None
    # "" clears the selection (back to "no platform chosen").
    preferred_broker: Optional[str] = None
    notification_prefs: Optional[NotificationPrefs] = None


# --- Broker Models (Sprint 7) ---
class BrokerOrderCreate(BaseModel):
    """Normalized order request routed through the Broker Engine.
    `instrument_token` is only needed for Upstox symbols that are not already
    in the user's holdings/positions."""
    symbol: str = Field(min_length=1, max_length=32)
    exchange: str = "NSE"
    transaction_type: str = Field(default="BUY", pattern="^(BUY|SELL)$")
    quantity: int = Field(gt=0, le=100000)
    order_type: str = Field(default="MARKET", pattern="^(MARKET|LIMIT|SL|SL-M)$")
    product: Optional[str] = None       # CNC/MIS/NRML (Zerodha) or D/I (Upstox)
    price: Optional[float] = Field(default=None, ge=0)
    trigger_price: Optional[float] = Field(default=None, ge=0)
    validity: str = Field(default="DAY", pattern="^(DAY|IOC)$")
    instrument_token: Optional[str] = None
    tag: Optional[str] = Field(default=None, max_length=20)


class BrokerOrderModify(BaseModel):
    quantity: Optional[int] = Field(default=None, gt=0, le=100000)
    price: Optional[float] = Field(default=None, ge=0)
    trigger_price: Optional[float] = Field(default=None, ge=0)
    order_type: Optional[str] = Field(default=None, pattern="^(MARKET|LIMIT|SL|SL-M)$")
    validity: Optional[str] = Field(default=None, pattern="^(DAY|IOC)$")
