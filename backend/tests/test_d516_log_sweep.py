"""D5.16 credential/PII sweep at DEBUG across the paths this sprint added."""
import logging
import pytest

FAKE = {
    "access_token": "eyJhbGciOiJIUzI1NiJ9.FAKEfakeFAKEfakeFAKE.sIgNaTuReFaKe123",
    "api_key": "kite_ak_FAKE_9f3c2b1a7e5d",
    "api_secret": "kite_as_FAKE_ffee0011223344556677",
    "refresh_token": "rt_FAKE_aabbccddeeff00112233",
    "client_id": "AB1234",
}
NEEDLES = tuple(FAKE.values()) + ("user-secret-9911",)


def _sweep(caplog):
    text = "\n".join(r.getMessage() for r in caplog.records)
    for needle in NEEDLES:
        assert needle not in text, f"{needle[:12]}… leaked into a log line:\n{text[:2000]}"
    return text


def test_an_upstream_failure_never_carries_its_detail_into_a_log(caplog):
    """The control under test is each adapter's choice to raise
    `type(exc).__name__` rather than `exc`.

    An httpx error stringifies to the request it failed on, URL and all, and the
    gateway logs the `BrokerError` it receives verbatim. So the question is not
    whether the gateway is careful — it is whether an adapter forwards upstream
    detail into it. D3's token-in-log-URL leak was exactly this shape.

    The first version of this probe put the token in the adapter's *own*
    exception message, which no adapter here does, and so failed for a reason
    unrelated to the control. This one poisons the layer below.
    """
    import asyncio
    from contextlib import asynccontextmanager

    from services.brokers import angelone, dhan, fyers, upstox, zerodha
    from services.brokers.feed_universe import FeedInstrument
    from services.brokers.gateway import broker_gateway
    from services.brokers.registry import broker_registry

    poisoned = (f"HTTPError connecting to https://master.example/x"
                f"?access_token={FAKE['access_token']}&api_key={FAKE['api_key']}")

    @asynccontextmanager
    async def _exploding(timeout):
        raise RuntimeError(poisoned)
        yield  # pragma: no cover

    modules = {"zerodha": zerodha, "upstox": upstox, "angelone": angelone,
               "fyers": fyers, "dhan": dhan}
    loop = asyncio.new_event_loop()
    with caplog.at_level(logging.DEBUG):
        for broker, module in modules.items():
            adapter = broker_registry.get(broker)
            original_client = module._broker_http_client
            original_cache = type(adapter)._catalogue_cache
            module._broker_http_client = _exploding
            # A fresh cache, or a previously loaded index answers and the
            # download this test is about never runs.
            type(adapter)._catalogue_cache = type(original_cache)(1.0)
            try:
                loop.run_until_complete(broker_gateway.resolve_instruments(
                    broker, [FeedInstrument.of("RELIANCE")], FAKE))
            finally:
                module._broker_http_client = original_client
                type(adapter)._catalogue_cache = original_cache

    text = _sweep(caplog)
    assert "catalogue" in text.lower() or "master" in text.lower(), (
        "no failure was logged at all — the probe could not have failed"
    )


def test_the_watchlist_stream_logs_no_account_or_credential(caplog):
    import asyncio
    import services.heartbeat_engine as heartbeat
    from tests._fakedb import FakeDB

    class _Sockets:
        user_connections = {"user-secret-9911": {object()}}
        active = {"user-secret-9911"}

        async def send_to_user(self, *a):
            pass

    db = FakeDB()
    loop = asyncio.new_event_loop()
    loop.run_until_complete(db.watchlist.insert_one(
        {"user_id": "user-secret-9911", "symbol": "RELIANCE"}))
    prev_ws, prev_db = heartbeat._ws, heartbeat._db
    heartbeat._ws, heartbeat._db = _Sockets(), db
    try:
        with caplog.at_level(logging.DEBUG):
            loop.run_until_complete(heartbeat.task_watchlist_stream())
    finally:
        heartbeat._ws, heartbeat._db = prev_ws, prev_db
    _sweep(caplog)


def test_no_master_url_carries_a_credential():
    """The instrument masters are public assets. A URL that carried a token
    would put one in every log line that names the fetch, and in any proxy's
    access log — which is how D3's token-in-log-URL leak happened."""
    from services.brokers import angelone, dhan, fyers, upstox, zerodha

    urls = [zerodha.INSTRUMENT_MASTER_URL, angelone.INSTRUMENT_MASTER_URL,
            dhan.INSTRUMENT_MASTER_URL]
    urls += list(upstox.INSTRUMENT_MASTER_URLS.values())
    urls += list(fyers.INSTRUMENT_MASTER_URLS.values())
    for url in urls:
        assert url.startswith("https://"), url
        assert "?" not in url, f"{url} carries a query string"
        for marker in ("token", "key", "secret", "auth", "session"):
            assert marker not in url.lower(), f"{url} looks credentialed ({marker})"


def test_an_unreachable_master_raises_a_real_broker_error():
    """The regression for a defect the sweep found, not a property it set out
    to test.

    Every adapter's download path read `code=BrokerErrorCode.UPSTREAM` — an enum
    member that has never existed. So the `except` block raised
    `AttributeError: UPSTREAM` *while constructing the error it appears to
    construct*, and the `BrokerError` the gateway is written to catch was never
    raised at all. The behaviour still degraded correctly, one layer further out
    and for the wrong reason, which is why it survived D5.15 and why every
    catalogue test here missed it: they all stubbed `_instrument_catalogue`
    itself, so not one of them ever ran the download's failure path.
    """
    import asyncio
    from contextlib import asynccontextmanager

    from services.brokers import angelone, dhan, fyers, upstox, zerodha
    from services.brokers.catalogue import CatalogueCache
    from services.brokers.errors import BrokerError
    from services.brokers.registry import broker_registry

    @asynccontextmanager
    async def _exploding(timeout):
        raise RuntimeError("connection reset")
        yield  # pragma: no cover

    loop = asyncio.new_event_loop()
    for broker, module in {"zerodha": zerodha, "upstox": upstox,
                           "angelone": angelone, "fyers": fyers,
                           "dhan": dhan}.items():
        adapter = broker_registry.get(broker)
        original_client, original_cache = module._broker_http_client, type(adapter)._catalogue_cache
        module._broker_http_client = _exploding
        type(adapter)._catalogue_cache = CatalogueCache(1.0)
        try:
            with pytest.raises(BrokerError) as caught:
                loop.run_until_complete(adapter._download_catalogue())
        finally:
            module._broker_http_client = original_client
            type(adapter)._catalogue_cache = original_cache
        assert caught.value.code, f"{broker}: the error carries no code"
        assert "connection reset" not in caught.value.user_message
        # `BrokerError.__init__` does `user_message or message`, so an adapter
        # that supplies none does not get an empty one — it gets the DEVELOPER
        # message, which is the string that carries the vendor error type and
        # goes to logs. Asserting the two are distinct is what makes that
        # fallback visible; asserting merely that `user_message` is truthy
        # passes for an adapter leaking its developer text verbatim.
        assert caught.value.user_message != str(caught.value), (
            f"{broker}: no user-safe message was supplied, so the developer "
            f"message is what a user would be shown"
        )
