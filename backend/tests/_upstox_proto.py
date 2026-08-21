"""Upstox MarketDataFeedV3 — an INDEPENDENT protobuf encoder, for tests only.

WHY THIS FILE EXISTS
--------------------
`services/brokers/upstox.py` decodes the Upstox v3 market feed with a small
proto3 reader of its own, because `protobuf` is deliberately not a production
dependency of this platform (PH2.8) and re-adding a C extension plus a generated
`_pb2` build artifact to read one `double` out of a map is a poor trade.

The risk in hand-decoding a wire format is getting the *schema* wrong — a field
number transposed, a nested path misread — and that class of mistake is
invisible to a test written by the same hand as the decoder. Encoding fixtures
with a helper of our own and decoding them with our own reader would prove only
that the two agree with each other.

So this module encodes with **Google's protobuf runtime**, from a transcription
of Upstox's **official** `MarketDataFeedV3.proto`
(upstox/upstox-python, `upstox_client/feeder/proto/MarketDataFeedV3.proto`).
The bytes a fixture produces are the bytes Upstox's own SDK would produce, and
the adapter has to decode them. If the adapter's field numbers are wrong, the
oracle does not follow it into being wrong.

The schema is built as a `FileDescriptorProto` at import rather than shipped as
a generated `_pb2` module because `protoc` is not a build dependency here, and a
checked-in generated file is an artifact that silently goes stale.

Test-only. `protobuf` is pinned in requirements-dev.txt and imported nowhere
under `backend/services/`.
"""

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

F = descriptor_pb2.FieldDescriptorProto

PACKAGE = "com.upstox.marketdatafeederv3udapi.rpc.proto"
REF = "." + PACKAGE


def _message(file_proto, name):
    message = file_proto.message_type.add()
    message.name = name
    return message


def _field(message, name, number, field_type, type_name=None, label=F.LABEL_OPTIONAL):
    field = message.field.add()
    field.name = name
    field.number = number
    field.type = field_type
    field.label = label
    if type_name:
        field.type_name = type_name
    return field


def _enum(file_proto, name, values):
    enum = file_proto.enum_type.add()
    enum.name = name
    for number, value_name in enumerate(values):
        value = enum.value.add()
        value.name = value_name
        value.number = number
    return enum


def _map_entry(parent, name, key_type, value_type, value_type_name=None):
    """A proto3 map field is a repeated message of {key = 1, value = 2}."""
    entry = parent.nested_type.add()
    entry.name = name
    entry.options.map_entry = True
    _field(entry, "key", 1, key_type)
    _field(entry, "value", 2, value_type, value_type_name)
    return entry


def _build_schema():
    """Upstox's official MarketDataFeedV3.proto, as a FileDescriptorProto.

    Transcribed field-for-field from the schema published in Upstox's own Python
    SDK. Built at import rather than shipped as a generated `_pb2` module because
    `protoc` is not a build dependency of this repository, and a checked-in
    generated file is an artifact that goes stale without anyone noticing.
    """
    file_proto = descriptor_pb2.FileDescriptorProto()
    file_proto.name = "MarketDataFeedV3.proto"
    file_proto.package = PACKAGE
    file_proto.syntax = "proto3"

    ltpc = _message(file_proto, "LTPC")
    _field(ltpc, "ltp", 1, F.TYPE_DOUBLE)
    _field(ltpc, "ltt", 2, F.TYPE_INT64)
    _field(ltpc, "ltq", 3, F.TYPE_INT64)
    _field(ltpc, "cp", 4, F.TYPE_DOUBLE)

    quote = _message(file_proto, "Quote")
    _field(quote, "bidQ", 1, F.TYPE_INT64)
    _field(quote, "bidP", 2, F.TYPE_DOUBLE)
    _field(quote, "askQ", 3, F.TYPE_INT64)
    _field(quote, "askP", 4, F.TYPE_DOUBLE)

    greeks = _message(file_proto, "OptionGreeks")
    for number, name in enumerate(("delta", "theta", "gamma", "vega", "rho"), 1):
        _field(greeks, name, number, F.TYPE_DOUBLE)

    ohlc = _message(file_proto, "OHLC")
    _field(ohlc, "interval", 1, F.TYPE_STRING)
    for number, name in enumerate(("open", "high", "low", "close"), 2):
        _field(ohlc, name, number, F.TYPE_DOUBLE)
    _field(ohlc, "vol", 6, F.TYPE_INT64)
    _field(ohlc, "ts", 7, F.TYPE_INT64)

    market_level = _message(file_proto, "MarketLevel")
    _field(market_level, "bidAskQuote", 1, F.TYPE_MESSAGE, REF + ".Quote", F.LABEL_REPEATED)

    market_ohlc = _message(file_proto, "MarketOHLC")
    _field(market_ohlc, "ohlc", 1, F.TYPE_MESSAGE, REF + ".OHLC", F.LABEL_REPEATED)

    market_full = _message(file_proto, "MarketFullFeed")
    _field(market_full, "ltpc", 1, F.TYPE_MESSAGE, REF + ".LTPC")
    _field(market_full, "marketLevel", 2, F.TYPE_MESSAGE, REF + ".MarketLevel")
    _field(market_full, "optionGreeks", 3, F.TYPE_MESSAGE, REF + ".OptionGreeks")
    _field(market_full, "marketOHLC", 4, F.TYPE_MESSAGE, REF + ".MarketOHLC")
    _field(market_full, "atp", 5, F.TYPE_DOUBLE)
    _field(market_full, "vtt", 6, F.TYPE_INT64)
    _field(market_full, "oi", 7, F.TYPE_DOUBLE)
    _field(market_full, "iv", 8, F.TYPE_DOUBLE)
    _field(market_full, "tbq", 9, F.TYPE_DOUBLE)
    _field(market_full, "tsq", 10, F.TYPE_DOUBLE)

    index_full = _message(file_proto, "IndexFullFeed")
    _field(index_full, "ltpc", 1, F.TYPE_MESSAGE, REF + ".LTPC")
    _field(index_full, "marketOHLC", 2, F.TYPE_MESSAGE, REF + ".MarketOHLC")

    full_feed = _message(file_proto, "FullFeed")
    full_feed.oneof_decl.add().name = "FullFeedUnion"
    _field(full_feed, "marketFF", 1, F.TYPE_MESSAGE, REF + ".MarketFullFeed").oneof_index = 0
    _field(full_feed, "indexFF", 2, F.TYPE_MESSAGE, REF + ".IndexFullFeed").oneof_index = 0

    first_level = _message(file_proto, "FirstLevelWithGreeks")
    _field(first_level, "ltpc", 1, F.TYPE_MESSAGE, REF + ".LTPC")
    _field(first_level, "firstDepth", 2, F.TYPE_MESSAGE, REF + ".Quote")
    _field(first_level, "optionGreeks", 3, F.TYPE_MESSAGE, REF + ".OptionGreeks")
    _field(first_level, "vtt", 4, F.TYPE_INT64)
    _field(first_level, "oi", 5, F.TYPE_DOUBLE)
    _field(first_level, "iv", 6, F.TYPE_DOUBLE)

    feed = _message(file_proto, "Feed")
    feed.oneof_decl.add().name = "FeedUnion"
    _field(feed, "ltpc", 1, F.TYPE_MESSAGE, REF + ".LTPC").oneof_index = 0
    _field(feed, "fullFeed", 2, F.TYPE_MESSAGE, REF + ".FullFeed").oneof_index = 0
    _field(feed, "firstLevelWithGreeks", 3, F.TYPE_MESSAGE, REF + ".FirstLevelWithGreeks").oneof_index = 0
    _field(feed, "requestMode", 4, F.TYPE_ENUM, REF + ".RequestMode")

    _enum(file_proto, "RequestMode", ("ltpc", "full_d5", "option_greeks", "full_d30"))
    _enum(file_proto, "Type", ("initial_feed", "live_feed", "market_info"))
    _enum(file_proto, "MarketStatus", ("PRE_OPEN_START", "PRE_OPEN_END", "NORMAL_OPEN",
                                       "NORMAL_CLOSE", "CLOSING_START", "CLOSING_END"))

    market_info = _message(file_proto, "MarketInfo")
    _map_entry(market_info, "SegmentStatusEntry", F.TYPE_STRING, F.TYPE_ENUM, REF + ".MarketStatus")
    _field(market_info, "segmentStatus", 1, F.TYPE_MESSAGE,
           REF + ".MarketInfo.SegmentStatusEntry", F.LABEL_REPEATED)

    response = _message(file_proto, "FeedResponse")
    _map_entry(response, "FeedsEntry", F.TYPE_STRING, F.TYPE_MESSAGE, REF + ".Feed")
    _field(response, "type", 1, F.TYPE_ENUM, REF + ".Type")
    _field(response, "feeds", 2, F.TYPE_MESSAGE, REF + ".FeedResponse.FeedsEntry", F.LABEL_REPEATED)
    _field(response, "currentTs", 3, F.TYPE_INT64)
    _field(response, "marketInfo", 4, F.TYPE_MESSAGE, REF + ".MarketInfo")

    pool = descriptor_pool.DescriptorPool()
    pool.Add(file_proto)
    return pool


_POOL = _build_schema()

#: The message the Upstox v3 market feed sends. Serializing one produces the
#: bytes Upstox's own SDK produces.
FeedResponse = message_factory.GetMessageClass(_POOL.FindMessageTypeByName(PACKAGE + ".FeedResponse"))
