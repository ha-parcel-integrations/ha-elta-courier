"""Synthetic, privacy-safe ELTA response structures.

Shaped after five real tracked parcels:
``date``/``time`` are ``DD-MM-YYYY``/``HH:MM`` (Greek local time, no offset),
and ``status`` labels are the real Greek vocabulary — a made-up label would
never exercise the real ``_STATUS_MAP`` keys. Tracking codes and ``place``
depot names below are invented, not copied from any real capture.
"""
from __future__ import annotations

ACTIVE_CODE = "ELTA-TEST-ACTIVE"
DELIVERED_CODE = "ELTA-TEST-DELIVERED"
NOT_FOUND_CODE = "ELTA-TEST-NOTFOUND"

# The exact Greek event-status labels observed across all five real captures.
STATUS_REGISTERED = "Δημιουργία ΣΥ.ΔΕ.ΤΑ."
STATUS_IN_TRANSIT = "Αποστολή βρίσκεται σε στάδιο μεταφοράς"
STATUS_ARRIVAL = "Άφιξη σε"
STATUS_OUT_FOR_DELIVERY = "Σε διανομέα προς παράδοση"
STATUS_DELIVERED = "Αποστολή παραδόθηκε"

DEPOT = "ΚΤΕΠ ΤΕΣΤ - ΔΙΑΛΟΓΗ"


def event(date: str, time: str, status: str, place: str = DEPOT) -> dict:
    return {"date": date, "time": time, "place": place, "status": status}


def active_sample(code: str = ACTIVE_CODE) -> dict:
    return {
        "tracking_code": code,
        "echoed_tracking_code": code,
        "events": [
            event("27-04-2026", "09:30", STATUS_REGISTERED),
            event("28-04-2026", "07:15", STATUS_IN_TRANSIT),
            event("28-04-2026", "15:45", STATUS_OUT_FOR_DELIVERY),
        ],
    }


def delivered_sample(code: str = DELIVERED_CODE) -> dict:
    sample = active_sample(code)
    sample["events"].append(event("29-04-2026", "15:45", STATUS_DELIVERED))
    return sample


def pickup_sample(code: str = ACTIVE_CODE) -> dict:
    return active_sample(code)


def not_found_envelope(code: str = NOT_FOUND_CODE) -> dict:
    """The real 'unknown code' HTTP envelope: a string result, not a list.

    ``ELTACourierApiClient.async_get_parcel`` returns ``None`` for this shape
    — see ``tests/test_api.py::test_get_parcel_returns_none_for_wrong_number_shape``.
    """
    return {"status": 1, "result": {code: {"status": 0, "result": "wrong number"}}}
