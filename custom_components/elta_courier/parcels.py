"""ELTA payload mapping and suite-wide parcel list helpers."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    HISTORY_MAX_EVENTS,
    TRACKING_URL,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)
NEW_ISSUE_URL = "https://github.com/ha-parcel-integrations/ha-elta-courier/issues/new?template=unrecognised_status.yml"

# The complete Greek status vocabulary observed across five real tracked
# parcels. Exact-match, not prefix: the location that "Άφιξη σε" (arrival at)
# refers to always arrives in the event's own ``place`` field, never appended
# to ``status`` itself, in every capture seen so far. Extend only from a new
# real fixture — never guess a label.
_STATUS_MAP: dict[str, ParcelStatus] = {
    "Δημιουργία ΣΥ.ΔΕ.ΤΑ.": ParcelStatus.REGISTERED,
    "Αποστολή βρίσκεται σε στάδιο μεταφοράς": ParcelStatus.IN_TRANSIT,
    "Άφιξη σε": ParcelStatus.IN_TRANSIT,
    "Σε διανομέα προς παράδοση": ParcelStatus.OUT_FOR_DELIVERY,
    "Αποστολή παραδόθηκε": ParcelStatus.DELIVERED,
}
_unmapped_statuses_logged: set[str] = set()

# Event timestamps ("date": "23-07-2026" DD-MM-YYYY, "time": "11:21") carry no
# offset but are always Greek local time — the scan happened where the carrier
# operates, so this is fixed to the carrier's own country and must not follow
# HA's configured timezone.
_CARRIER_TZ = ZoneInfo("Europe/Athens")


def _warn_unmapped_status(label: str) -> None:
    """Log each unconfirmed carrier label once per HA session."""
    if label in _unmapped_statuses_logged:
        return
    _unmapped_statuses_logged.add(label)
    _LOGGER.warning(
        "Unrecognised ELTA Courier status — help us map it. Open an issue and "
        "paste this line: %s\\n  status=%s → reported as 'unknown'",
        NEW_ISSUE_URL,
        label,
    )


def map_parcel_status(label: str | None) -> ParcelStatus:
    """Map only fixture-confirmed labels; ELTA's map starts empty."""
    if not label:
        return ParcelStatus.UNKNOWN
    mapped = _STATUS_MAP.get(label)
    if mapped is None:
        _warn_unmapped_status(label)
        return ParcelStatus.UNKNOWN
    return mapped


def map_event_status(label: str | None) -> ParcelStatus | None:
    """Return a history mapping without inventing a status."""
    return _STATUS_MAP.get(label) if label else None


def _event_time(event: dict[str, Any]) -> datetime | None:
    """Parse ELTA's DD-MM-YYYY / HH:MM event date+time as Greek local time."""
    date, time = event.get("date"), event.get("time")
    if not date or not time:
        return None
    try:
        naive = datetime.strptime(f"{date} {time}", "%d-%m-%Y %H:%M")
    except ValueError:
        return None
    return naive.replace(tzinfo=_CARRIER_TZ)


def _published_timestamp(event: dict[str, Any]) -> str | None:
    parsed = _event_time(event)
    return parsed.isoformat() if parsed and parsed.tzinfo is not None else None


def build_history(
    events: list | None, *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict]:
    """Return ELTA history oldest-first, preserving unknown timestamps as null."""
    indexed = [event for event in events or [] if isinstance(event, dict)]
    indexed.sort(
        key=lambda event: (
            _event_time(event) is None,
            _event_time(event) or datetime.max,
        )
    )
    return [
        {
            "timestamp": _published_timestamp(event),
            "status": map_event_status(event.get("status")),
            "raw_status": event.get("status") or None,
        }
        for event in indexed[-max_events:]
    ]


def tracking_url(_tracking_code: str | None) -> str:
    """Do not put a private tracking code into a public-looking deep link."""
    return TRACKING_URL


def normalize_parcel(raw: dict, *, include_history: bool = False) -> dict:
    """Map ELTA's event envelope without inferring unconfirmed fields."""
    code = raw.get("tracking_code")
    echoed = raw.get("echoed_tracking_code")
    if echoed and code and echoed != code:
        _LOGGER.warning(
            "ELTA Courier response tracking-code mismatch; keeping the configured code"
        )
    events = [event for event in raw.get("events", []) if isinstance(event, dict)]
    history = build_history(events)
    newest = history[-1] if history else None
    raw_status = newest["raw_status"] if newest else None
    status = map_parcel_status(raw_status)
    delivered = status is ParcelStatus.DELIVERED
    return {
        "carrier": "ELTA Courier",
        "barcode": code,
        "sender": None,
        "receiver": None,
        "status": status,
        "raw_status": raw_status,
        "delivered": delivered,
        "delivered_at": newest["timestamp"] if delivered and newest else None,
        "planned_from": None,
        "planned_to": None,
        "pickup": False,
        "pickup_point": None,
        "url": tracking_url(code),
        "weight": None,
        "dimensions": None,
        "history": history if include_history else None,
        "raw": [
            {key: event.get(key) for key in ("date", "time", "place", "status")}
            for event in events
        ],
    }


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO timestamp, treating a naive value as UTC for sorting."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Sort parseable values first, leaving missing timestamps at the end."""
    dated: list[tuple[datetime, dict]] = []
    undated: list[dict] = []
    for parcel in parcels:
        parsed = parse_iso(parcel.get(key_field))
        if parsed is None:
            undated.append(parcel)
        else:
            dated.append((parsed, parcel))
    dated.sort(key=lambda item: item[0], reverse=descending)
    return [parcel for _, parcel in dated] + undated


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Keep delivered parcels according to the shared retention option."""
    amount = int(
        entry.options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if entry.options.get(CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE) != "days":
        return parcels[:amount]
    cutoff = datetime.now(timezone.utc) - timedelta(days=amount)
    return [
        parcel
        for parcel in parcels
        if (parsed := parse_iso(parcel.get("delivered_at"))) is None or parsed >= cutoff
    ]
