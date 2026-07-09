from pydantic import BaseModel, Field, ConfigDict, BeforeValidator
from typing import Optional, List, Annotated
from datetime import datetime, timezone
from bson import ObjectId


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

class UserLogin(BaseModel):
    email: str
    password: str

class UserResponse(BaseDocument):
    name: str
    email: str
    role: str = "user"
    capital: float = 100000
    risk_level: str = "moderate"
    max_daily_loss: float = 5000
    max_trades_per_day: int = 3
    telegram_chat_id: Optional[str] = None
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
class TradeCreate(BaseModel):
    symbol: str
    stock_name: str
    type: str = "BUY"
    entry_price: float
    quantity: int
    stop_loss: float
    target1: float
    target2: Optional[float] = None
    notes: Optional[str] = None
    setup_type: Optional[str] = None
    is_paper: bool = False

class TradeResponse(BaseDocument):
    user_id: Optional[str] = None
    symbol: str
    stock_name: str
    type: str = "BUY"
    entry_price: float
    exit_price: Optional[float] = None
    quantity: int
    stop_loss: float
    target1: float
    target2: Optional[float] = None
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


# --- Settings Models ---
class UserSettingsUpdate(BaseModel):
    name: Optional[str] = None
    capital: Optional[float] = None
    risk_level: Optional[str] = None
    max_daily_loss: Optional[float] = None
    max_trades_per_day: Optional[int] = None
    telegram_chat_id: Optional[str] = None
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
