"""Tests for ELTA's bare form POST client."""
import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.elta_courier.api import ELTACourierApiClient, ELTACourierApiError

CODE = "ELTA-TEST-123"


def _session_returning(
    status: int, body: object = None, headers: dict | None = None, *, prefix: str = ""
) -> MagicMock:
    """Build a fake aiohttp session whose response.text() carries ``body``.

    ``body`` is JSON-encoded when it is a dict/list; a plain string is used
    verbatim so tests can exercise a genuinely non-JSON body. ``prefix`` lets
    a test prepend bytes (e.g. a BOM) ahead of the JSON, as the live endpoint
    does.
    """
    text = prefix + (body if isinstance(body, str) else json.dumps(body))
    response = AsyncMock(status=status, headers=headers or {})
    response.text = AsyncMock(return_value=text)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=response)
    context.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.post.return_value = context
    return session


async def test_get_parcel_posts_one_code_without_credentials():
    session = _session_returning(200, {"status": 1, "result": {CODE: {"status": 2, "result": []}}})
    parcel = await ELTACourierApiClient(session).async_get_parcel(CODE)
    assert parcel == {"tracking_code": CODE, "echoed_tracking_code": CODE, "events": []}
    assert session.post.call_args.args == ("https://www.elta-courier.gr/track.php",)
    assert session.post.call_args.kwargs["data"] == {"number": CODE}
    assert "Authorization" not in session.post.call_args.kwargs["headers"]


@pytest.mark.parametrize("body", [{}, {"result": {}}, {"result": {CODE: {}}}, []])
async def test_get_parcel_returns_none_for_missing_result(body):
    assert await ELTACourierApiClient(_session_returning(200, body)).async_get_parcel(CODE) is None


async def test_get_parcel_returns_none_for_wrong_number_shape():
    """Real 'not found' payload: per-parcel result is the string 'wrong number'."""
    body = {"status": 1, "result": {CODE: {"status": 0, "result": "wrong number"}}}
    assert await ELTACourierApiClient(_session_returning(200, body)).async_get_parcel(CODE) is None


async def test_get_parcel_strips_leading_bom_and_newline():
    """Live responses lead with `ef bb bf 0a` before the JSON starts."""
    session = _session_returning(
        200,
        {"status": 1, "result": {CODE: {"status": 1, "result": []}}},
        prefix="﻿\n",
    )
    parcel = await ELTACourierApiClient(session).async_get_parcel(CODE)
    assert parcel == {"tracking_code": CODE, "echoed_tracking_code": CODE, "events": []}


async def test_get_parcel_reads_body_as_text_not_declared_content_type():
    """ELTA declares text/html; the client must not rely on aiohttp's json()."""
    session = _session_returning(200, {"result": {CODE: {"result": []}}})
    await ELTACourierApiClient(session).async_get_parcel(CODE)
    session.post.return_value.__aenter__.return_value.text.assert_awaited_once()


async def test_get_parcel_raises_for_http_and_non_json():
    with pytest.raises(ELTACourierApiError):
        await ELTACourierApiClient(_session_returning(500, {})).async_get_parcel(CODE)
    with pytest.raises(ELTACourierApiError):
        await ELTACourierApiClient(_session_returning(200, "not json")).async_get_parcel(CODE)


async def test_get_parcel_propagates_network_error():
    session = MagicMock()
    session.post.side_effect = aiohttp.ClientError("boom")
    with pytest.raises(aiohttp.ClientError):
        await ELTACourierApiClient(session).async_get_parcel(CODE)


async def test_get_parcel_429_carries_seconds_retry_after():
    session = _session_returning(429, {}, headers={"Retry-After": "30"})
    with pytest.raises(ELTACourierApiError) as excinfo:
        await ELTACourierApiClient(session).async_get_parcel(CODE)
    assert excinfo.value.status_code == 429
    assert excinfo.value.retry_after == 30.0


async def test_get_parcel_429_falls_back_when_retry_after_is_not_seconds():
    session = _session_returning(429, {}, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
    with pytest.raises(ELTACourierApiError) as excinfo:
        await ELTACourierApiClient(session).async_get_parcel(CODE)
    assert excinfo.value.status_code == 429
    assert excinfo.value.retry_after is None


async def test_get_parcel_429_without_retry_after_header():
    session = _session_returning(429, {})
    with pytest.raises(ELTACourierApiError) as excinfo:
        await ELTACourierApiClient(session).async_get_parcel(CODE)
    assert excinfo.value.retry_after is None


async def test_get_parcel_echoes_the_sole_key_when_it_differs_from_the_request():
    session = _session_returning(200, {"result": {"OTHER-CODE": {"result": []}}})
    parcel = await ELTACourierApiClient(session).async_get_parcel(CODE)
    assert parcel == {"tracking_code": CODE, "echoed_tracking_code": "OTHER-CODE", "events": []}
