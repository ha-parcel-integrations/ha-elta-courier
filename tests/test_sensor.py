"""Tests for ELTA Courier sensor property logic."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

from custom_components.elta_courier.const import ParcelStatus
from custom_components.elta_courier.sensor import (
    ELTACourierAwaitingPickupSensor,
    ELTACourierDeliveredParcelsSensor,
    ELTACourierEnRouteToPickupPointSensor,
    ELTACourierIncomingParcelsSensor,
    ELTACourierLastUpdateSensor,
    ELTACourierNextDeliverySensor,
    ELTACourierParcelSensor,
)


def _entry(entry_id: str = "e1") -> MagicMock:
    entry = MagicMock()
    entry.entry_id = entry_id
    return entry


def _coordinator(data: list[dict], delivered: list[dict] | None = None) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = data
    coordinator.delivered = delivered if delivered is not None else []
    return coordinator


def _parcel(
    barcode: str,
    status: ParcelStatus = ParcelStatus.IN_TRANSIT,
    pickup: bool = False,
    planned_from: str | None = None,
) -> dict:
    return {
        "carrier": "ELTA Courier",
        "barcode": barcode,
        "sender": "Sender",
        "receiver": None,
        "status": status,
        "pickup": pickup,
        "planned_from": planned_from,
    }


def test_incoming_counts_and_lists():
    coordinator = _coordinator([_parcel("A"), _parcel("B")])
    sensor = ELTACourierIncomingParcelsSensor(
        coordinator, _entry(), lambda _: None, set()
    )
    assert sensor.native_value == 2
    assert len(sensor.extra_state_attributes["parcels"]) == 2


def test_parcel_sensor_status_and_attributes():
    parcel = _parcel("A", status=ParcelStatus.OUT_FOR_DELIVERY)
    sensor = ELTACourierParcelSensor(_coordinator([parcel]), _entry(), "A")
    assert sensor.native_value == ParcelStatus.OUT_FOR_DELIVERY
    assert sensor.extra_state_attributes["barcode"] == "A"


def test_parcel_sensor_missing_barcode():
    sensor = ELTACourierParcelSensor(_coordinator([_parcel("A")]), _entry(), "OTHER")
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_next_delivery_picks_earliest():
    coordinator = _coordinator(
        [
            _parcel("A", planned_from="2099-05-02T10:00:00Z"),
            _parcel("B", planned_from="2099-05-01T10:00:00Z"),
        ]
    )
    sensor = ELTACourierNextDeliverySensor(coordinator, _entry())
    assert sensor.native_value == datetime(2099, 5, 1, 10, 0, tzinfo=UTC)
    assert sensor.extra_state_attributes["barcode"] == "B"


def test_next_delivery_none_without_moments():
    sensor = ELTACourierNextDeliverySensor(_coordinator([_parcel("A")]), _entry())
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_next_delivery_skips_unparseable_moment():
    coordinator = _coordinator(
        [
            _parcel("A", planned_from="not-a-date"),
            _parcel("B", planned_from="2099-05-01T10:00:00Z"),
        ]
    )
    sensor = ELTACourierNextDeliverySensor(coordinator, _entry())
    assert sensor.extra_state_attributes["barcode"] == "B"


def test_delivered_sensor():
    coordinator = _coordinator(
        [], delivered=[_parcel("D", status=ParcelStatus.DELIVERED)]
    )
    sensor = ELTACourierDeliveredParcelsSensor(coordinator, _entry())
    assert sensor.native_value == 1
    assert sensor.extra_state_attributes["parcels"][0]["barcode"] == "D"


def test_last_update_sensor():
    coordinator = _coordinator([])
    moment = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)
    coordinator.last_success_time = moment
    sensor = ELTACourierLastUpdateSensor(coordinator, _entry())
    assert sensor.native_value == moment


def test_awaiting_pickup_counts_only_at_pickup_point_status():
    coordinator = _coordinator(
        [
            _parcel("A", status=ParcelStatus.AT_PICKUP_POINT),
            _parcel("B", status=ParcelStatus.IN_TRANSIT, pickup=True),
        ]
    )
    sensor = ELTACourierAwaitingPickupSensor(coordinator, _entry())
    assert sensor.native_value == 1
    assert sensor.extra_state_attributes["parcels"][0]["barcode"] == "A"
    assert sensor.unique_id == "e1_awaiting_pickup"


def test_next_delivery_skips_a_window_that_has_ended():
    coordinator = _coordinator(
        [
            _parcel("OLD", planned_from="2000-01-01T10:00:00Z"),
            _parcel("NEW", planned_from="2099-01-01T10:00:00Z"),
        ]
    )
    sensor = ELTACourierNextDeliverySensor(coordinator, _entry())
    assert sensor.extra_state_attributes["barcode"] == "NEW"


def test_en_route_and_awaiting_pickup_share_no_parcel():
    parcels = [
        _parcel("A", status=ParcelStatus.IN_TRANSIT, pickup=True),
        _parcel("B", status=ParcelStatus.AT_PICKUP_POINT, pickup=True),
        _parcel("C", status=ParcelStatus.RETURNING, pickup=True),
        _parcel("D", status=ParcelStatus.IN_TRANSIT),
    ]
    en_route = ELTACourierEnRouteToPickupPointSensor(_coordinator(parcels), _entry())
    awaiting = ELTACourierAwaitingPickupSensor(_coordinator(parcels), _entry())
    assert en_route.unique_id == "e1_en_route_to_pickup_point"
    assert [p["barcode"] for p in en_route.extra_state_attributes["parcels"]] == ["A"]
    assert [p["barcode"] for p in awaiting.extra_state_attributes["parcels"]] == ["B"]
    assert en_route.native_value == 1
    assert (
        ELTACourierEnRouteToPickupPointSensor(_coordinator([]), _entry()).native_value
        == 0
    )
