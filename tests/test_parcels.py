"""Tests for ELTA's conservative payload mapping."""
import logging

from custom_components.elta_courier.const import (
    CAPABILITIES,
    KNOWN_CAPABILITIES,
    ParcelStatus,
)
from custom_components.elta_courier.parcels import (
    build_history,
    map_parcel_status,
    normalize_parcel,
)

from .payloads import (
    ACTIVE_CODE,
    STATUS_ARRIVAL,
    STATUS_DELIVERED,
    STATUS_IN_TRANSIT,
    STATUS_OUT_FOR_DELIVERY,
    STATUS_REGISTERED,
    active_sample,
    event,
)

CANONICAL_KEYS = ["carrier", "barcode", "sender", "receiver", "status", "raw_status", "delivered", "delivered_at", "planned_from", "planned_to", "pickup", "pickup_point", "url", "weight", "dimensions", "history", "raw"]


def test_capabilities_are_limited_to_confirmed_fields():
    assert CAPABILITIES == frozenset({"url", "history"})
    assert CAPABILITIES <= KNOWN_CAPABILITIES


def test_normalize_keeps_only_confirmed_fields_and_raw_events():
    parcel = normalize_parcel(active_sample(), include_history=True)
    assert list(parcel) == CANONICAL_KEYS
    assert parcel["barcode"] == ACTIVE_CODE
    assert parcel["status"] is ParcelStatus.OUT_FOR_DELIVERY
    assert parcel["delivered"] is False
    assert parcel["sender"] is parcel["receiver"] is None
    assert parcel["planned_from"] is parcel["planned_to"] is None
    assert parcel["url"] == "https://www.elta-courier.gr/track"
    assert parcel["raw"] == active_sample()["events"]
    # Real DD-MM-YYYY/HH:MM events parse to an aware Europe/Athens timestamp.
    assert parcel["history"][0]["timestamp"] == "2026-04-27T09:30:00+03:00"


def test_history_sorts_out_of_order_events_and_caps_at_twenty():
    events = [event("28-04-2026", "10:00", "later"), event("27-04-2026", "10:00", "earlier")]
    assert [item["raw_status"] for item in build_history(events)] == ["earlier", "later"]
    many = [event("01-04-2026", f"{hour:02d}:00", str(hour)) for hour in range(25)]
    assert len(build_history(many)) == 20


def test_unmapped_status_warns_once(caplog):
    caplog.set_level(logging.WARNING)
    assert map_parcel_status("new label") is ParcelStatus.UNKNOWN
    assert map_parcel_status("new label") is ParcelStatus.UNKNOWN
    assert caplog.text.count("new label") == 1
    assert "issues/new" in caplog.text


def test_missing_fields_do_not_break_normalization():
    parcel = normalize_parcel({"tracking_code": ACTIVE_CODE, "events": [{"status": "only label"}]})
    assert parcel["history"] is None
    assert parcel["raw_status"] == "only label"


def test_confirmed_status_map_covers_exactly_the_real_vocabulary():
    """The five real Greek labels observed across all five captured parcels."""
    assert map_parcel_status(STATUS_REGISTERED) is ParcelStatus.REGISTERED
    assert map_parcel_status(STATUS_IN_TRANSIT) is ParcelStatus.IN_TRANSIT
    assert map_parcel_status(STATUS_ARRIVAL) is ParcelStatus.IN_TRANSIT
    assert map_parcel_status(STATUS_OUT_FOR_DELIVERY) is ParcelStatus.OUT_FOR_DELIVERY
    assert map_parcel_status(STATUS_DELIVERED) is ParcelStatus.DELIVERED


def test_arrival_status_does_not_prefix_match_a_trailing_place():
    """'Άφιξη σε' is exact-match: a real payload never appends a place to it."""
    assert map_parcel_status(f"{STATUS_ARRIVAL} ΑΘΗΝΑΣ") is ParcelStatus.UNKNOWN


def test_event_timestamps_parse_dd_mm_yyyy_as_athens_local_time():
    """Real events carry DD-MM-YYYY/HH:MM with no offset — Greek local time."""
    history = build_history([event("23-07-2026", "11:21", STATUS_DELIVERED)])
    # Late July is Greek summer time (EEST, UTC+3) — not a UTC or naive read.
    assert history[0]["timestamp"] == "2026-07-23T11:21:00+03:00"


def test_event_with_unparseable_date_keeps_null_timestamp():
    history = build_history([event("not-a-date", "11:21", STATUS_DELIVERED)])
    assert history[0]["timestamp"] is None
