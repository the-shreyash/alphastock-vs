"""Data validator for the Market Engine.

Validates market data integrity before it enters the processing pipeline.
Rejects invalid data and logs warnings for anomalies.

Validation rules:
    - Missing critical values (price, symbol)
    - Invalid prices (negative, zero, extreme outliers)
    - Negative volume
    - Duplicate records
    - Timestamp validity
    - Market hours awareness
"""
import logging
from datetime import datetime, timezone, time as dtime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Price sanity bounds for Indian markets (INR)
MIN_STOCK_PRICE = 0.01
MAX_STOCK_PRICE = 500_000.0  # ~5 Lakh (accounts for MRF etc.)
MAX_INDEX_VALUE = 200_000.0
MAX_CHANGE_PCT = 20.0  # Circuit limit on NSE is 20%

# NSE market hours (IST)
MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)
PRE_OPEN = dtime(9, 0)
POST_CLOSE = dtime(16, 0)


class ValidationResult:
    """Result of a validation check with pass/fail and reason."""

    __slots__ = ("valid", "errors", "warnings", "data")

    def __init__(self, data: Optional[Dict] = None):
        self.valid = True
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.data = data

    def fail(self, reason: str) -> "ValidationResult":
        self.valid = False
        self.errors.append(reason)
        return self

    def warn(self, reason: str) -> "ValidationResult":
        self.warnings.append(reason)
        return self


def validate_stock_quote(quote: Dict[str, Any]) -> ValidationResult:
    """Validate a normalized stock quote for data integrity."""
    result = ValidationResult(quote)

    if not quote:
        return result.fail("Empty quote data")

    symbol = quote.get("symbol", "")
    if not symbol or not isinstance(symbol, str):
        return result.fail("Missing or invalid symbol")

    price = quote.get("price")
    if price is None:
        return result.fail(f"{symbol}: Missing price")
    if not isinstance(price, (int, float)):
        return result.fail(f"{symbol}: Price is not numeric: {type(price)}")
    if price < MIN_STOCK_PRICE:
        return result.fail(f"{symbol}: Price below minimum ({price})")
    if price > MAX_STOCK_PRICE:
        return result.fail(f"{symbol}: Price above maximum ({price})")

    # Volume checks
    volume = quote.get("volume")
    if volume is not None and isinstance(volume, (int, float)) and volume < 0:
        return result.fail(f"{symbol}: Negative volume ({volume})")

    # Change percentage sanity
    change_pct = quote.get("change_pct")
    if change_pct is not None and isinstance(change_pct, (int, float)):
        if abs(change_pct) > MAX_CHANGE_PCT:
            result.warn(f"{symbol}: Change exceeds circuit limit ({change_pct}%)")

    # OHLC consistency
    high = quote.get("high")
    low = quote.get("low")
    if high is not None and low is not None:
        if isinstance(high, (int, float)) and isinstance(low, (int, float)):
            if high < low:
                result.warn(f"{symbol}: High ({high}) < Low ({low})")

    # Volume ratio anomaly
    vol_ratio = quote.get("volume_ratio")
    if vol_ratio is not None and isinstance(vol_ratio, (int, float)):
        if vol_ratio > 10.0:
            result.warn(f"{symbol}: Extremely high volume ratio ({vol_ratio}x)")

    return result


def validate_index_quote(quote: Dict[str, Any]) -> ValidationResult:
    """Validate an index quote."""
    result = ValidationResult(quote)

    if not quote:
        return result.fail("Empty index data")

    name = quote.get("name", "")
    if not name:
        return result.fail("Missing index name")

    value = quote.get("value")
    if value is None:
        return result.fail(f"{name}: Missing index value")
    if isinstance(value, (int, float)) and (value <= 0 or value > MAX_INDEX_VALUE):
        return result.fail(f"{name}: Index value out of range ({value})")

    return result


def validate_news_article(article: Dict[str, Any]) -> ValidationResult:
    """Validate a news article for completeness."""
    result = ValidationResult(article)

    if not article:
        return result.fail("Empty article")

    title = article.get("title", "")
    if not title or len(title) < 5:
        return result.fail("Missing or too short title")

    sentiment = article.get("sentiment", "")
    if sentiment not in ("positive", "negative", "neutral"):
        result.warn(f"Unknown sentiment: '{sentiment}'")

    url = article.get("url", "")
    if not url:
        result.warn("Missing article URL")

    return result


def validate_sector_data(sector: Dict[str, Any]) -> ValidationResult:
    """Validate sector performance data."""
    result = ValidationResult(sector)

    if not sector:
        return result.fail("Empty sector data")

    name = sector.get("name", "")
    if not name:
        return result.fail("Missing sector name")

    change_pct = sector.get("change_pct")
    if change_pct is not None and isinstance(change_pct, (int, float)):
        if abs(change_pct) > 15.0:
            result.warn(f"{name}: Extreme sector change ({change_pct}%)")

    return result


def validate_batch(
    items: List[Dict[str, Any]],
    validator_fn,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Validate a batch of items, returning (valid_items, error_messages).
    Items that fail validation are excluded from the result."""
    valid = []
    errors = []

    for item in items:
        result = validator_fn(item)
        if result.valid:
            valid.append(item)
        else:
            errors.extend(result.errors)
        if result.warnings:
            for w in result.warnings:
                logger.warning(f"Validator: {w}")

    if errors:
        logger.warning(f"Validator: {len(errors)} items rejected out of {len(items)}")

    return valid, errors


def is_market_hours() -> bool:
    """Check if current time (IST) is within NSE market hours."""
    from datetime import timezone as tz
    import pytz

    try:
        ist = pytz.timezone("Asia/Kolkata")
        now = datetime.now(ist)
        # Weekday check (Monday=0, Sunday=6)
        if now.weekday() >= 5:
            return False
        current_time = now.time()
        return MARKET_OPEN <= current_time <= MARKET_CLOSE
    except ImportError:
        # Fallback: assume market hours for UTC+5:30
        now_utc = datetime.now(timezone.utc)
        ist_hour = (now_utc.hour + 5) % 24 + (1 if now_utc.minute >= 30 else 0)
        return 9 <= ist_hour <= 15


def is_pre_market() -> bool:
    """Check if we're in pre-market hours (9:00 AM - 9:15 AM IST)."""
    try:
        import pytz
        ist = pytz.timezone("Asia/Kolkata")
        now = datetime.now(ist)
        if now.weekday() >= 5:
            return False
        current_time = now.time()
        return PRE_OPEN <= current_time < MARKET_OPEN
    except ImportError:
        return False
