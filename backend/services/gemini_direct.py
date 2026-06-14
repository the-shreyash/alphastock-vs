"""Direct Google Gemini integration for real-time market analysis."""
import os
import logging

logger = logging.getLogger(__name__)


def _get_key():
    return os.environ.get("GOOGLE_GEMINI_KEY", "").strip()


def is_configured():
    return bool(_get_key())


async def gemini_analyze(prompt: str, model: str = "gemini-2.0-flash"):
    """Call Gemini directly with Google AI Studio key."""
    key = _get_key()
    if not key:
        return None

    try:
        from google import genai
        client = genai.Client(api_key=key)
        response = client.models.generate_content(
            model=model,
            contents=prompt,
        )
        return response.text
    except Exception as e:
        error_str = str(e)
        if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
            logger.warning(f"Gemini quota exceeded — will retry later. Free tier limit reached.")
            return "[Gemini quota reached] Your Google AI Studio free tier limit has been reached. The quota resets daily. To get higher limits, enable billing at https://ai.google.dev/pricing"
        logger.error(f"Gemini direct error: {e}")
        return f"Gemini analysis unavailable: {str(e)[:100]}"


async def gemini_realtime_analysis(stock_data: dict):
    """Real-time Gemini analysis of a stock with current market data."""
    key = _get_key()
    if not key:
        return None

    prompt = f"""You are an expert Indian stock market analyst. Analyze this stock in real-time:

Stock: {stock_data.get('name', stock_data.get('symbol', 'Unknown'))} ({stock_data.get('symbol', '')})
Current Price: INR {stock_data.get('price', 'N/A')}
Change: {stock_data.get('change', 0)} ({stock_data.get('change_pct', 0)}%)
Open: {stock_data.get('open', 'N/A')} | High: {stock_data.get('high', 'N/A')} | Low: {stock_data.get('low', 'N/A')}
Volume: {stock_data.get('volume', 'N/A')}
RSI: {stock_data.get('rsi', 'N/A')}
Market State: {stock_data.get('market_state', 'CLOSED')}

Provide:
1. Quick assessment (1 sentence)
2. Key support and resistance levels
3. Intraday outlook
4. Risk factors
5. Actionable recommendation (BUY/SELL/HOLD with reasoning)

Be specific with price levels. Use INR. Keep under 200 words."""

    try:
        from google import genai
        client = genai.Client(api_key=key)
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
        )
        return response.text
    except Exception as e:
        if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
            return "[Gemini quota reached] Free tier limit hit. Resets daily. Enable billing at ai.google.dev/pricing for more."
        logger.error(f"Gemini realtime analysis error: {e}")
        return None


async def gemini_market_pulse():
    """Generate a quick market pulse using Gemini."""
    key = _get_key()
    if not key:
        return None

    prompt = """You are an Indian stock market AI analyst. Generate a brief "Market Pulse" update:

Consider current global factors, Indian market trends, upcoming events, and sector rotation.
Include:
1. Overall market sentiment (1 line)
2. Top sector to watch today
3. One key risk factor
4. One opportunity

Keep it under 100 words. Professional tone. Use specific index levels if you know them."""

    try:
        from google import genai
        client = genai.Client(api_key=key)
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
        )
        return response.text
    except Exception as e:
        if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
            return "[Gemini quota reached] Free tier limit hit. Resets daily."
        logger.error(f"Gemini market pulse error: {e}")
        return None
