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


# --- Settings Models ---
class UserSettingsUpdate(BaseModel):
    name: Optional[str] = None
    capital: Optional[float] = None
    risk_level: Optional[str] = None
    max_daily_loss: Optional[float] = None
    max_trades_per_day: Optional[int] = None
    telegram_chat_id: Optional[str] = None
    notification_prefs: Optional[NotificationPrefs] = None
