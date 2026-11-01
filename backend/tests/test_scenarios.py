"""Сценарии показа из ТЗ: суммы должны совпасть до копейки, баланс денег — сохраниться."""
from .helpers import RUB, TRIP_SHEET, WAYBILL, assert_invariants, balances, event, new_order, run_trip


def test_1_ideal_trip(alfa, carrier, operator):
    before = balances(alfa)["available"]
    oid = alfa.ok("post", "/api/orders", {"carrier_id": 3, "route_from": "Москва", "route_to": "Тверь",
                                          "cargo": "Продукты", "price": 120_000 * RUB})["id"]
    o = alfa.ok("get", f"/api/orders/{oid}")
    assert o["reserve_total"] == 132_000 * RUB  # «К резервированию: 132 000 ₽»

    alfa.ok("post", f"/api/orders/{oid}/reserve")
    b = balances(alfa)
    assert b["reserved"] == 132_000 * RUB and b["available"] == before - 132_000 * RUB

    assert carrier.ok("get", f"/api/orders/{oid}")["reserve_balance"] == 132_000 * RUB  # «Оплата гарантирована»
    carrier.ok("post", f"/api/orders/{oid}/accept")
    run_trip(carrier, oid)
    carrier.ok("post", f"/api/orders/{oid}/propose-price", {"amount": 120_000 * RUB})
    r = alfa.ok("post", f"/api/orders/{oid}/confirm-price")

    assert r == {"status": "paid", "paid": 120_000 * RUB, "refund": 12_000 * RUB, "invoiced": 0}
    assert balances(carrier)["available"] == 120_000 * RUB
    assert balances(alfa) == {"available": before - 120_000 * RUB, "reserved": 0, "invoice": 0}
    msgs = [d["message"] for d in alfa.ok("get", f"/api/orders/{oid}")["decisions"]]
    assert "Выплачено 120 000 ₽: рейс подтверждён, стоимость согласована." in msgs
    assert_invariants(operator)


def test_2_downtime(alfa, carrier, operator):
    oid = new_order(alfa, carrier, 85_000)
    run_trip(carrier, oid, loading_min=270)  # норма 120 → 150 мин сверх нормы
    carrier.ok("post", f"/api/orders/{oid}/propose-price", {"amount": 85_000 * RUB})
    f = [x for x in carrier.ok("get", f"/api/orders/{oid}")["findings"] if x["rule"] == "downtime_погрузка"][0]
    assert f["amount"] == 2_500 * RUB and f["guilty"] == "customer" and "150 мин" in f["message"]

    r = alfa.ok("post", f"/api/orders/{oid}/confirm-price")
    assert r["paid"] == 87_500 * RUB and r["refund"] == 6_000 * RUB
    assert_invariants(operator)


def test_3_driver_mismatch_then_corrected(alfa, carrier, operator):
    oid = new_order(alfa, carrier, 100_000)
    wrong = {**TRIP_SHEET, "driver_name": "Петров П.П.", "driver_license": "77АА 000001"}
    run_trip(carrier, oid, trip_sheet=wrong)
    carrier.ok("post", f"/api/orders/{oid}/propose-price", {"amount": 100_000 * RUB})
    r = alfa.ok("post", f"/api/orders/{oid}/confirm-price")
    assert r["status"] == "blocked"
    reason = r["reasons"][0]
    assert "Петров П.П." in reason and "Иванов Ю.И." in reason  # оба имени простым языком
    assert balances(carrier)["available"] == 0

    # Оператор видит причину в ленте
    assert any("Петров" in d["message"] and d["kind"] == "payout_blocked" for d in operator.ok("get", "/api/decisions"))

    # Исправленный путевой лист → сверка заново → выплата
    r = event(carrier, oid, "trip_sheet_issued", "2026-05-21T15:00:00Z", trip_sheet=TRIP_SHEET)
    assert r.json()["status"] == "paid"
    assert balances(carrier)["available"] == 100_000 * RUB
    assert_invariants(operator)


def test_4_dispute(alfa, carrier, operator):
    oid = new_order(alfa, carrier, 90_000)
    run_trip(carrier, oid)
    carrier.ok("post", f"/api/orders/{oid}/propose-price", {"amount": 95_000 * RUB})
    alfa.ok("post", f"/api/orders/{oid}/dispute", {"comment": "В договоре 90 000 ₽"})
    assert balances(alfa)["reserved"] == 99_000 * RUB  # деньги остаются в резерве
    assert any(o["status"] == "disputed" for o in operator.ok("get", "/api/orders"))

    r = operator.ok("post", f"/api/orders/{oid}/resolve", {"amount": 92_000 * RUB, "comment": "Компромисс по документам"})
    assert r["paid"] == 92_000 * RUB and r["refund"] == 7_000 * RUB
    assert_invariants(operator)


def test_5_duplicate_waybill(alfa, carrier, operator):
    first = new_order(alfa, carrier, 50_000)
    run_trip(carrier, first)
    second = new_order(alfa, carrier, 50_000)
    r = event(carrier, second, "waybill_signed", "2026-05-22T06:00:00Z", waybill=WAYBILL)
    assert r.json() == {"status": "rejected", "duplicate": True}

    o2 = operator.ok("get", f"/api/orders/{second}")
    assert o2["is_duplicate"] and o2["status"] == "rejected" and o2["reserve_balance"] == 0
    assert operator.ok("get", f"/api/orders/{first}")["status"] == "delivered"  # первая не затронута
    assert any(d["kind"] == "duplicate" for d in operator.ok("get", "/api/decisions"))
    assert_invariants(operator)


def test_insufficient_funds_explained(beta, carrier, operator):
    oid = beta.ok("post", "/api/orders", {"carrier_id": 3, "route_from": "А", "route_to": "Б",
                                          "cargo": "Груз", "price": 60_000 * RUB})["id"]
    r = beta.post(f"/api/orders/{oid}/reserve")
    assert r.status_code == 409 and "Не хватает 16 000 ₽" in r.json()["message"]
    assert any(d["kind"] == "refused" for d in beta.ok("get", f"/api/orders/{oid}")["decisions"])
    assert balances(beta) == {"available": 50_000 * RUB, "reserved": 0, "invoice": 0}
    assert_invariants(operator)


def test_operator_metrics_match_demo(alfa, carrier, operator):
    a = new_order(alfa, carrier, 120_000); run_trip(carrier, a, waybill={**WAYBILL, "number": "M-1"})
    carrier.ok("post", f"/api/orders/{a}/propose-price", {"amount": 120_000 * RUB}); alfa.ok("post", f"/api/orders/{a}/confirm-price")
    b = new_order(alfa, carrier, 85_000); run_trip(carrier, b, waybill={**WAYBILL, "number": "M-2"}, loading_min=270)
    carrier.ok("post", f"/api/orders/{b}/propose-price", {"amount": 85_000 * RUB}); alfa.ok("post", f"/api/orders/{b}/confirm-price")
    c = new_order(alfa, carrier, 90_000); run_trip(carrier, c, waybill={**WAYBILL, "number": "M-3"})
    carrier.ok("post", f"/api/orders/{c}/propose-price", {"amount": 95_000 * RUB})
    alfa.ok("post", f"/api/orders/{c}/dispute", {"comment": "нет"})
    m = operator.ok("get", "/api/operator/metrics")
    assert m["confirmed"] == 2 and m["reached_delivery"] == 3
    assert m["downtime_comp_sum"] == 2_500 * RUB
    assert m["disputes"] == 1 and m["dispute_share"] == round(1 / 3, 4)
    assert m["avg_agree_to_pay_seconds"] is not None and m["avg_agree_to_pay_seconds"] < 5


def test_reset_restores_initial_state(alfa, carrier, operator):
    oid = new_order(alfa, carrier, 120_000)
    operator.ok("post", "/api/admin/reset")
    assert balances(alfa) == {"available": 5_000_000 * RUB, "reserved": 0, "invoice": 0}
    assert alfa.ok("get", "/api/orders") == []
    assert_invariants(operator)
