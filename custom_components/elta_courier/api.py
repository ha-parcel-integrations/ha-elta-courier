"""ELTA Courier's public, form-based tracking client."""
from __future__ import annotations

import json
from typing import Any

import aiohttp

from .const import TRACKING_API_URL


class ELTACourierApiError(Exception):
    """Raised when an ELTA Courier API call returns an unexpected response."""

    def __init__(
        self,
        detail: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        """Store the status code and the ``Retry-After`` header, if any."""
        super().__init__(f"ELTA Courier API request failed: {detail}")
        self.detail = detail
        self.status_code = status_code
        self.retry_after = retry_after


class ELTACourierApiClient:
    """Read one user-supplied ELTA tracking code at a time."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the client with an aiohttp session."""
        self._session = session

    async def async_get_parcel(self, tracking_code: str) -> dict[str, Any] | None:
        """Return the per-code event envelope, or ``None`` when it is absent.

        ELTA sends JSON as ``text/html``. Numeric ``status`` fields have
        unconfirmed semantics, so only the event list is returned.
        """
        async with self._session.post(
            TRACKING_API_URL,
            data={"number": tracking_code},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ) as response:
            if response.status == 429:
                retry_after_header = response.headers.get("Retry-After")
                try:
                    retry_after = float(retry_after_header) if retry_after_header else None
                except ValueError:
                    retry_after = None  # an HTTP-date, not seconds; let the caller's own backoff handle it
                raise ELTACourierApiError(
                    "HTTP 429", status_code=429, retry_after=retry_after
                )
            if not 200 <= response.status < 300:
                raise ELTACourierApiError(
                    f"HTTP {response.status}", status_code=response.status
                )
            # The live body is served as text/html and leads with a UTF-8 BOM
            # plus a newline before the JSON starts, which aiohttp's own
            # json() rejects outright — read as text and decode ourselves.
            body = await response.text()
            try:
                payload = json.loads(body.lstrip("﻿").strip())
            except ValueError as err:
                raise ELTACourierApiError("unparseable body") from err

        if not isinstance(payload, dict) or not isinstance(payload.get("result"), dict):
            return None
        results = payload["result"]
        echoed_tracking_code = tracking_code
        parcel = results.get(tracking_code)
        if parcel is None and len(results) == 1:
            echoed_tracking_code, parcel = next(iter(results.items()))
        if not isinstance(parcel, dict) or not isinstance(parcel.get("result"), list):
            return None
        return {
            "tracking_code": tracking_code,
            "echoed_tracking_code": echoed_tracking_code,
            "events": parcel["result"],
        }
