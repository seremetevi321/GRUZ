"""Права доступа, идемпотентность, гонки и защита данных на уровне БД."""
import threading
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import tx
from .helpers import RUB, WAYBILL, assert_invariants, balances, event, new_order, run_trip


# ---------- доступ ----------

def test_customer_cannot_see_foreign_orders(alfa, beta, carrier):
    oid = new_order(alfa, carrier, 10_000)
    assert beta.get(f"/api/orders/{oid}").status_code == 404
    assert beta.ok("get", "/api/orders") == []
    assert all(d["order_id"] != oid for d in beta.ok("get", "/api/decisions"))


def test_other_carrier_cannot_touch_order(alfa, carrier, other_carrier):
    oid = alfa.ok("post", "/api/orders", {"carrier_id": 3, "route_from": "А", "route_to": "Б", "cargo": "Г", "price": 10_000 * RUB})["id"]
    alfa.ok("post", f"/api/orders/{oid}/reserve")
    assert other_carrier.post(f"/api/orders/{oid}/accept").status_code == 404
    assert event(other_carrier, oid, "departed", "2026-05-21T07:00:00Z").status_code == 404


def test_carrier_cannot_confirm_price(alfa, carrier):
    oid = new_order(alfa, carrier, 10_000)
    run_trip(carrier, oid)
    carrier.ok("post", f"/api/orders/{oid}/propose-price", {"amount": 10_000 * RUB})
    assert carrier.post(f"/api/orders/{oid}/confirm-price").status_code == 403
    assert carrier.post(f"/api/orders/{oid}/resolve", {"amount": 1, "comment": "x"}).status_code == 403


def test_customer_cannot_resolve_or_reset(alfa):
    assert alfa.post("/api/admin/reset").status_code == 403
    assert alfa.get("/api/operator/metrics").status_code == 403
    assert alfa.get("/api/operator/invariants").status_code == 403


def test_operator_sees_everything(alfa, beta, carrier, operator):
    new_order(alfa, carrier, 10_000)
    new_order(beta, carrier, 10_000)
    assert len(operator.ok("get", "/api/orders")) == 2


def test_auth_required_and_bad_token(client):
    assert client.get("/api/orders").status_code == 401
    assert client.get("/api/orders", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_wrong_password_and_rate_limit(client):
    for _ in range(10):
        assert client.post("/api/auth/login", json={"login": "alfa", "password": "bad"}).status_code == 401
    assert client.post("/api/auth/login", json={"login": "alfa", "password": "demo2026"}).status_code == 429


def test_tokens_stored_hashed(alfa):
    token = alfa.h["Authorization"][7:]
    with tx() as conn:
        raw = conn.execute(text("SELECT count(*) FROM sessions WHERE token_hash = convert_to(:t, 'UTF8')"), {"t": token}).scalar()
        hashes = conn.execute(text("SELECT password_hash FROM users")).scalars().all()
    assert raw == 0
    assert all(h.startswith("$argon2id$") for h in hashes)


def test_unknown_fields_and_bad_amounts_rejected(alfa):
    base = {"carrier_id": 3, "route_from": "А", "route_to": "Б", "cargo": "Г"}
    assert alfa.post("/api/orders", {**base, "price": -5}).status_code == 422
    assert alfa.post("/api/orders", {**base, "price": 1.5}).status_code == 422
    assert alfa.post("/api/orders", {**base, "price": 100, "status": "paid"}).status_code == 422


def test_cannot_pay_more_than_reserve(alfa, carrier):
    oid = new_order(alfa, carrier, 10_000)
    run_trip(carrier, oid)
    assert carrier.post(f"/api/orders/{oid}/propose-price", {"amount": 11_001 * RUB}).status_code == 422


def test_cannot_skip_states(alfa, carrier):
    oid = alfa.ok("post", "/api/orders", {"carrier_id": 3, "route_from": "А", "route_to": "Б", "cargo": "Г", "price": 10_000 * RUB})["id"]
    assert carrier.post(f"/api/orders/{oid}/accept").status_code == 409  # ещё не зарезервировано
    alfa.ok("post", f"/api/orders/{oid}/reserve")
    assert carrier.post(f"/api/orders/{oid}/propose-price", {"amount": 100}).status_code == 409  # не доставлено


# ---------- повторы ----------

def test_same_idempotency_key_does_not_double(alfa, carrier, operator):
    oid = alfa.ok("post", "/api/orders", {"carrier_id": 3, "route_from": "А", "route_to": "Б", "cargo": "Г", "price": 10_000 * RUB})["id"]
    key = str(uuid.uuid4())
    r1 = alfa.post(f"/api/orders/{oid}/reserve", key=key)
    r2 = alfa.post(f"/api/orders/{oid}/reserve", key=key)
    assert r1.status_code == r2.status_code == 200 and r1.json() == r2.json()
    assert r2.headers.get("Idempotent-Replay") == "true"
    assert balances(alfa)["reserved"] == 11_000 * RUB
    # Новый ключ — второе нажатие отклоняется машиной состояний, а не проходит повторно.
    assert alfa.post(f"/api/orders/{oid}/reserve").status_code == 409
    assert_invariants(operator)


def test_idempotency_key_reuse_for_other_request(alfa):
    key = str(uuid.uuid4())
    base = {"carrier_id": 3, "route_from": "А", "route_to": "Б", "cargo": "Г"}
    assert alfa.post("/api/orders", {**base, "price": 100}, key=key).status_code == 200
    assert alfa.post("/api/orders", {**base, "price": 200}, key=key).status_code == 422


def test_duplicate_event_is_noop(alfa, carrier, operator):
    oid = new_order(alfa, carrier, 10_000)
    body = {"event_id": "ev-fixed-0001", "kind": "waybill_signed", "occurred_at": "2026-05-21T06:00:00Z", "waybill": WAYBILL}
    assert carrier.post(f"/api/orders/{oid}/events", body).json()["status"] == "accepted"
    assert carrier.post(f"/api/orders/{oid}/events", body).json()["duplicate"] is True
    docs = operator.ok("get", f"/api/orders/{oid}")["documents"]
    assert docs["waybill"]["_version"] == 1


def test_double_confirm_pays_once(alfa, carrier, operator):
    oid = new_order(alfa, carrier, 10_000)
    run_trip(carrier, oid)
    carrier.ok("post", f"/api/orders/{oid}/propose-price", {"amount": 10_000 * RUB})
    assert alfa.post(f"/api/orders/{oid}/confirm-price").status_code == 200
    assert alfa.post(f"/api/orders/{oid}/confirm-price").status_code == 409
    assert balances(carrier)["available"] == 10_000 * RUB
    assert_invariants(operator)


def test_concurrent_confirms_pay_once(alfa, carrier, operator):
    oid = new_order(alfa, carrier, 10_000)
    run_trip(carrier, oid)
    carrier.ok("post", f"/api/orders/{oid}/propose-price", {"amount": 10_000 * RUB})
    codes = []
    def hit():
        codes.append(alfa.post(f"/api/orders/{oid}/confirm-price").status_code)
    threads = [threading.Thread(target=hit) for _ in range(8)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert codes.count(200) == 1 and codes.count(409) == 7
    assert balances(carrier)["available"] == 10_000 * RUB
    assert_invariants(operator)


# ---------- защита на уровне базы ----------

def test_db_rejects_unbalanced_txn():
    with pytest.raises(DBAPIError, match="unbalanced"):
        with tx() as conn:
            t = conn.execute(text("INSERT INTO ledger_txns(kind, memo) VALUES ('hack','x') RETURNING id")).scalar()
            a = conn.execute(text("SELECT id FROM accounts WHERE kind='available' LIMIT 1")).scalar()
            conn.execute(text("INSERT INTO ledger_lines(txn_id, account_id, amount) VALUES (:t, :a, 100)"), {"t": t, "a": a})


def test_db_rejects_direct_balance_update():
    with pytest.raises(DBAPIError, match="only change through ledger"):
        with tx() as conn:
            conn.execute(text("UPDATE accounts SET balance = balance + 100 WHERE kind='available'"))


def test_db_ledger_and_feed_append_only(alfa, carrier):
    new_order(alfa, carrier, 10_000)
    for sql in ("DELETE FROM ledger_lines", "UPDATE ledger_txns SET memo='x'", "DELETE FROM decisions"):
        with pytest.raises(DBAPIError, match="append-only"):
            with tx() as conn:
                conn.execute(text(sql))


def test_db_rejects_negative_available():
    with pytest.raises(DBAPIError, match="non_negative"):
        with tx() as conn:
            t = conn.execute(text("INSERT INTO ledger_txns(kind, memo) VALUES ('hack','x') RETURNING id")).scalar()
            beta = conn.execute(text("SELECT a.id FROM accounts a JOIN companies c ON c.id=a.company_id "
                                     "WHERE c.name='Бета-Продукты' AND a.kind='available'")).scalar()
            fund = conn.execute(text("SELECT id FROM accounts WHERE kind='funding'")).scalar()
            conn.execute(text("INSERT INTO ledger_lines(txn_id, account_id, amount) VALUES (:t,:a,:x),(:t,:f,:y)"),
                         {"t": t, "a": beta, "x": -10_000_000, "f": fund, "y": 10_000_000})
