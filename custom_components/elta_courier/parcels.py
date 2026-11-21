"""ELTA payload mapping and suite-wide parcel list helpers."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta, tzinfo
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


def _moment(value: Any, zone: tzinfo) -> tuple[datetime, bool, str] | None:
    """Return ``(aware datetime, date_only, text)`` for a carrier time, or ``None``.

    A naive time or a bare date gets ``zone``; an aware time keeps its offset
    and, when it came in as a string, its own spelling.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        moment = datetime.combine(value, time.min, tzinfo=zone)
        return moment, True, moment.isoformat()
    elif isinstance(value, str) and value:
        try:
            if len(value) == 10:
                moment = datetime.combine(
                    date.fromisoformat(value), time.min, tzinfo=zone
                )
                return moment, True, moment.isoformat()
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo:
            return parsed, False, value
    else:
        return None
    moment = parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)
    return moment, False, moment.isoformat()


def _end_of_day(moment: datetime, zone: tzinfo) -> datetime:
    local = moment.astimezone(zone)
    return datetime.combine(local.date(), time(23, 59, 59), tzinfo=zone)


def planned_window(
    start: Any, end: Any, *, zone: tzinfo
) -> tuple[str | None, str | None]:
    """Return ``(planned_from, planned_to)`` in the canonical window shape.

    ``start`` and ``end`` are ISO strings, dates or datetimes, or ``None``; an
    epoch number must be converted first (see ``to_iso_timestamp``). A window
    keeps both ends. One moment with a time ends at ``23:59:59`` the same local
    day; a date covers ``00:00:00`` to ``23:59:59``; two dates run from the
    first day's start to the last day's end. Equal values are one moment,
    reversed values are swapped, and ``planned_to`` is never set without
    ``planned_from``. ``zone`` is the carrier's own zone: it dates naive times
    and bare dates and decides which day a moment ends on, so a ``Z`` moment
    still ends on the carrier's local day. When nothing is left of that day
    after the moment, there is no ``planned_to``.
    """
    first = _moment(start, zone)
    last = _moment(end, zone)
    if first is None:
        first, last = last, None
    if first is None:
        return None, None

    def closing(moment: tuple[datetime, bool, str]) -> datetime:
        return _end_of_day(moment[0], zone) if moment[1] else moment[0]

    if last is not None and closing(last) < first[0]:
        first, last = last, first
    if last is not None and closing(last) == first[0]:
        last = None

    if last is None:
        end_of_day = _end_of_day(first[0], zone)
        return first[2], end_of_day.isoformat() if end_of_day > first[0] else None
    return first[2], closing(last).isoformat() if last[1] else last[2]


_NO_WINDOW_STATUSES = frozenset(
    {ParcelStatus.AT_PICKUP_POINT, ParcelStatus.RETURNING, ParcelStatus.DELIVERED}
)


def planned_for_status(
    status: ParcelStatus, planned_from: str | None, planned_to: str | None
) -> tuple[str | None, str | None]:
    """Empty the delivery window once the parcel is at its destination or done."""
    if status in _NO_WINDOW_STATUSES:
        return None, None
    return planned_from, planned_to


def is_pickup(
    route_flag: bool | None,
    status: ParcelStatus,
    event_statuses: Iterable[ParcelStatus | None] = (),
) -> bool:
    """Whether the delivery route ends at a pickup point.

    The carrier's own route flag wins; otherwise the parcel having reached
    ``AT_PICKUP_POINT`` at any point counts, so the value survives collection.
    """
    if route_flag:
        return True
    return status is ParcelStatus.AT_PICKUP_POINT or any(
        event is ParcelStatus.AT_PICKUP_POINT for event in event_statuses
    )


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
    planned_from, planned_to = planned_for_status(
        status, *planned_window(None, None, zone=_CARRIER_TZ)
    )
    pickup = is_pickup(
        None, status, (map_event_status(event.get("status")) for event in ordered)
    )
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
        "planned_from": planned_from,
        "planned_to": planned_to,
        "pickup": pickup,
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
