"""The parcel contract's checks, run over every normalised sample."""

from datetime import datetime

import pytest

from custom_components.elta_courier.const import ParcelStatus
from custom_components.elta_courier.parcels import (
    _CARRIER_TZ,
    normalize_parcel,
    parse_iso,
    planned_for_status,
    planned_window,
)

from .payloads import (
    STATUS_AT_STORE,
    STATUS_DELIVERED,
    STATUS_RECIPIENT_ABSENT,
    active_sample,
    delivered_sample,
    event,
    pickup_sample,
)

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "contents",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]


def _collected() -> dict:
    sample = pickup_sample()
    sample["events"].append(event("30-04-2026", "16:00", STATUS_DELIVERED))
    return sample


def _problem() -> dict:
    sample = active_sample()
    sample["events"].append(event("29-04-2026", "12:00", STATUS_RECIPIENT_ABSENT))
    return sample


SAMPLES = {
    "delivered": delivered_sample,
    "active": active_sample,
    "at_pickup_point": pickup_sample,
    "collected": _collected,
    "problem": _problem,
    "bare": lambda: {"tracking_code": "ELTA-TEST-BARE"},
}


@pytest.fixture(params=SAMPLES)
def parcel(request) -> dict:
    return normalize_parcel(SAMPLES[request.param]())


def test_keys_and_order(parcel):
    assert list(parcel) == CANONICAL_KEYS


def test_delivered_follows_status(parcel):
    assert parcel["delivered"] is (parcel["status"] is ParcelStatus.DELIVERED)


def test_delivered_at_only_when_delivered(parcel):
    assert (parcel["delivered_at"] is not None) is parcel["delivered"]


def test_planned_window_is_ordered_and_anchored(parcel):
    if parcel["planned_to"] is not None:
        assert parcel["planned_from"] is not None
        assert parse_iso(parcel["planned_to"]) > parse_iso(parcel["planned_from"])


def test_pickup_is_a_bool_and_the_point_needs_it(parcel):
    assert isinstance(parcel["pickup"], bool)
    if not parcel["pickup"]:
        assert parcel["pickup_point"] is None


def test_a_collected_parcel_stays_pickup_with_its_collection_time():
    parcel = normalize_parcel(_collected())
    assert parcel["status"] is ParcelStatus.DELIVERED
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == "2026-04-30T16:00:00+03:00"
    assert parcel["pickup"] is True


def test_a_parcel_that_never_reached_the_store_is_not_pickup():
    assert normalize_parcel(delivered_sample())["pickup"] is False


def test_pickup_survives_history_being_off_and_on():
    for include_history in (False, True):
        parcel = normalize_parcel(_collected(), include_history=include_history)
        assert parcel["pickup"] is True


def test_the_store_label_in_an_older_event_is_enough():
    sample = _collected()
    assert STATUS_AT_STORE in [e["status"] for e in sample["events"]]
    sample["events"] = [e for e in sample["events"] if e["status"] != STATUS_AT_STORE]
    assert normalize_parcel(sample)["pickup"] is False


def test_planned_is_emptied_by_status():
    window = ("2099-01-01T10:00:00+02:00", "2099-01-01T23:59:59+02:00")
    for status in (
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
        ParcelStatus.DELIVERED,
    ):
        assert planned_for_status(status, *window) == (None, None)
    assert planned_for_status(ParcelStatus.IN_TRANSIT, *window) == window


def test_a_moment_ends_at_the_end_of_that_day():
    planned_from, planned_to = planned_window(
        "2099-01-01T10:00:00", None, zone=_CARRIER_TZ
    )
    end = datetime.fromisoformat(planned_to)
    assert (end.hour, end.minute, end.second, end.microsecond) == (23, 59, 59, 0)
    assert end.date() == parse_iso(planned_from).date()
    assert end.utcoffset() == parse_iso(planned_from).utcoffset()


def test_a_date_covers_the_whole_day():
    planned_from, planned_to = planned_window("2099-01-01", None, zone=_CARRIER_TZ)
    start = datetime.fromisoformat(planned_from)
    end = datetime.fromisoformat(planned_to)
    assert (start.hour, start.minute, start.second) == (0, 0, 0)
    assert (end.hour, end.minute, end.second, end.microsecond) == (23, 59, 59, 0)
    assert start.date() == end.date()
