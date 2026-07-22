"""Unit tests for security.identifiers (PH1.12 / finding F-2).

`parse_object_id` is the single choke point that turns an untrusted identifier
into a `bson.ObjectId`. These tests pin its contract: valid ids parse, an
existing ObjectId passes through unchanged, and every flavor of malformed input
becomes a clean HTTP 400 (never a 500, never a leaked raw value).
"""
import pytest
from bson import ObjectId
from fastapi import HTTPException

from security.identifiers import parse_object_id


def test_valid_hex_string_parses_to_objectid():
    oid = ObjectId()
    parsed = parse_object_id(str(oid), "user")
    assert isinstance(parsed, ObjectId)
    assert parsed == oid


def test_existing_objectid_passes_through_unchanged():
    oid = ObjectId()
    assert parse_object_id(oid, "trade") is oid


@pytest.mark.parametrize("bad", ["", "not-an-id", "123", "z" * 24, "  ", "deadbeef"])
def test_malformed_string_raises_400(bad):
    with pytest.raises(HTTPException) as exc:
        parse_object_id(bad, "trade")
    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid trade id"


@pytest.mark.parametrize("bad", [None, 123, 4.5, [], {}, object()])
def test_non_string_types_raise_400(bad):
    with pytest.raises(HTTPException) as exc:
        parse_object_id(bad, "resource")
    assert exc.value.status_code == 400


def test_resource_name_shapes_error_detail():
    with pytest.raises(HTTPException) as exc:
        parse_object_id("bad", "announcement")
    assert exc.value.detail == "Invalid announcement id"


def test_default_resource_name_is_generic():
    with pytest.raises(HTTPException) as exc:
        parse_object_id("bad")
    assert exc.value.detail == "Invalid resource id"


def test_error_detail_never_echoes_the_invalid_value():
    # A reflected value could be an XSS or log-injection vector; the message must
    # be static and never contain the attacker-supplied input.
    payload = "<script>alert(1)</script>"
    with pytest.raises(HTTPException) as exc:
        parse_object_id(payload, "user")
    assert payload not in exc.value.detail
