import asyncio, pytest
from unittest.mock import AsyncMock, patch
import server

class Boom(Exception): pass

@pytest.mark.parametrize("name,kw", [
    ("timeout", {"side_effect": asyncio.TimeoutError()}),
    ("connerr", {"side_effect": ConnectionError("x")}),
    ("boom", {"side_effect": Boom("x")}),
    ("none", {"return_value": None}),
])
def test_overview(client, monkeypatch, name, kw):
    monkeypatch.setattr(server, "real_overview", AsyncMock(**kw))
    try:
        r = client.get("/api/market/overview"); print(f"\n>>> overview {name} -> {r.status_code}")
    except Exception as e: print(f"\n>>> overview {name} -> RAISED {type(e).__name__}: {e}")

@pytest.mark.parametrize("name,kw", [
    ("none", {"return_value": None}),
    ("timeout", {"side_effect": asyncio.TimeoutError()}),
    ("boom", {"side_effect": Boom("x")}),
])
def test_stock(client, monkeypatch, name, kw):
    monkeypatch.setattr(server, "real_quote", AsyncMock(**kw))
    try:
        r = client.get("/api/stocks/RELIANCE"); print(f"\n>>> stock {name} -> {r.status_code}")
    except Exception as e: print(f"\n>>> stock {name} -> RAISED {type(e).__name__}: {e}")

@pytest.mark.parametrize("name,kw", [("timeout", {"side_effect": asyncio.TimeoutError()}), ("boom", {"side_effect": Boom("x")})])
def test_gainers(client, name, kw):
    with patch("services.real_market.fetch_real_gainers", new_callable=AsyncMock, **kw):
        try:
            r = client.get("/api/market/gainers"); print(f"\n>>> gainers {name} -> {r.status_code}")
        except Exception as e: print(f"\n>>> gainers {name} -> RAISED {type(e).__name__}: {e}")
