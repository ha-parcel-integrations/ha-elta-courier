"""ELTA payload mapping and suite-wide parcel list helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
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

# Exact-match, not prefix: the location that "Άφιξη σε" (arrival at) refers to
# always arrives in the event's own ``place`` field, never appended to
# ``status`` itself. Extend only from a label a real payload has shown.
_STATUS_MAP: dict[str, ParcelStatus] = {
    "Δημιουργία ΣΥ.ΔΕ.ΤΑ.": ParcelStatus.REGISTERED,
    "Παραλαβή αποστολής": ParcelStatus.IN_TRANSIT,
    "Αποστολή βρίσκεται σε στάδιο μεταφοράς": ParcelStatus.IN_TRANSIT,
    "Άφιξη σε": ParcelStatus.IN_TRANSIT,
    "Σε διανομέα προς παράδοση": ParcelStatus.OUT_FOR_DELIVERY,
    # Recipient not home: the parcel waits for them to contact the store.
    "Απουσία παραλήπτη - Επικοινωνία με το κατάστημα": ParcelStatus.PROBLEM,
    "Αποστολή βρίσκεται στο κατάστημα-Δυνατότητα παραλαβής": (
        ParcelStatus.AT_PICKUP_POINT
    ),
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
    """Map a confirmed label, warning once for any other."""
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


def _sorted_events(events: list | None) -> list[dict[str, Any]]:
    """Return the events oldest-first, untimed ones last."""
    indexed = [event for event in events or [] if isinstance(event, dict)]
    indexed.sort(
        key=lambda event: (
            _event_time(event) is None,
            _event_time(event) or datetime.max,
        )
    )
    return indexed


def build_history(
    events: list | None, *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict]:
    """Return ELTA history oldest-first, preserving unknown timestamps as null."""
    indexed = _sorted_events(events)
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
    ordered = _sorted_events(events)
    newest_event = ordered[-1] if ordered else None
    raw_status = (newest_event or {}).get("status") or None
    status = map_parcel_status(raw_status)
    status_event = newest_event
    if status is ParcelStatus.UNKNOWN:
        for event in reversed(ordered):
            mapped = map_event_status(event.get("status"))
            if mapped is not None:
                status, status_event = mapped, event
                break
    delivered = status is ParcelStatus.DELIVERED
    at_pickup_point = status is ParcelStatus.AT_PICKUP_POINT
    return {
        "carrier": "ELTA Courier",
        "barcode": code,
        "sender": None,
        "receiver": None,
        "status": status,
        "raw_status": raw_status,
        "delivered": delivered,
        "delivered_at": (
            _published_timestamp(status_event) if delivered and status_event else None
        ),
        "planned_from": None,
        "planned_to": None,
        "pickup": at_pickup_point,
        "pickup_point": None,
        "url": tracking_url(code),
        "weight": None,
        "dimensions": None,
        "history": build_history(ordered) if include_history else None,
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
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


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
    if (
        entry.options.get(CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE)
        != "days"
    ):
        return parcels[:amount]
    cutoff = datetime.now(UTC) - timedelta(days=amount)
    return [
        parcel
        for parcel in parcels
        if (parsed := parse_iso(parcel.get("delivered_at"))) is None or parsed >= cutoff
    ]
