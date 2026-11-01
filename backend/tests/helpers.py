import uuid

RUB = 100

WAYBILL = {"number": "512", "trip_sheet_number": "150", "driver_name": "Иванов Ю.И.",
           "driver_license": "57МК № 113540", "vehicle_plate": "X133MT77RUS", "vehicle_model": "КамАЗ 53-215"}
TRIP_SHEET = {"number": "150", "driver_name": "Иванов Юрий Иванович", "driver_license": "57МК113540",
              "vehicle_plate": "Х 133 МТ 77", "vehicle_model": "КамАЗ 53-215", "vehicle_type": "truck",
              "medical_check": True}


def carrier_id(api, name="ТрансЛайн"):
    return next(c["id"] for c in api.ok("get", "/api/carriers") if c["name"] == name)


def new_order(customer, carrier, price_rub, carrier_name="ТрансЛайн"):
    oid = customer.ok("post", "/api/orders", {"carrier_id": carrier_id(customer, carrier_name), "route_from": "Москва",
                                              "route_to": "Тверь", "cargo": "Продукты", "price": price_rub * RUB})["id"]
    customer.ok("post", f"/api/orders/{oid}/reserve")
    carrier.ok("post", f"/api/orders/{oid}/accept")
    return oid


def event(api, oid, kind, at, **doc):
    body = {"event_id": f"ev-{uuid.uuid4()}", "kind": kind, "occurred_at": at, **doc}
    return api.post(f"/api/orders/{oid}/events", body)


def run_trip(api, oid, waybill=None, trip_sheet=None, loading_min=60, unloading_min=60):
    """Полный трек рейса через симулятор. Время погрузки и выгрузки задаётся в минутах."""
    assert event(api, oid, "waybill_signed", "2026-05-21T06:00:00Z", waybill=waybill or WAYBILL).status_code == 200
    assert event(api, oid, "trip_sheet_issued", "2026-05-21T06:10:00Z", trip_sheet=trip_sheet or TRIP_SHEET).status_code == 200
    def t(m):  # минуты от 07:00
        return f"2026-05-21T{7 + m // 60:02d}:{m % 60:02d}:00Z"
    unl_start = 60 + loading_min + 120
    for kind, at in [("departed", t(0)), ("arrived_loading", t(60)), ("departed_loading", t(60 + loading_min)),
                     ("arrived_unloading", t(unl_start)), ("departed_unloading", t(unl_start + unloading_min)),
                     ("delivered", t(unl_start + unloading_min + 5))]:
        r = event(api, oid, kind, at)
        assert r.status_code == 200, r.text


def balances(api):
    return api.ok("get", "/api/me")["balances"]


def assert_invariants(operator):
    inv = operator.ok("get", "/api/operator/invariants")
    assert inv["ok"], inv
    assert inv["client_money"] == 505_000_000  # 5 000 000 ₽ + 50 000 ₽
