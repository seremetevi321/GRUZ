"""Тестовые данные из ТЗ. Сброс очищает только бизнес-данные: пользователи и
их сессии остаются, поэтому три открытых окна не разлогиниваются."""
from sqlalchemy import text
from sqlalchemy.engine import Connection

from . import config, ledger
from .security import hash_password

COMPANIES = [
    ("Альфа-Ритейл", "customer", 500_000_000),   # 5 000 000 ₽
    ("Бета-Продукты", "customer", 5_000_000),    # 50 000 ₽
    ("ТрансЛайн", "carrier", 0),
    ("СеверТранс", "carrier", 0),
    ("Быстрая Доставка", "carrier", 0),
    ("Банк", "bank", 0),
]
USERS = [
    ("alfa", "Анна Смирнова", "customer", "Альфа-Ритейл"),
    ("beta", "Борис Котов", "customer", "Бета-Продукты"),
    ("transline", "Иван Петров", "carrier", "ТрансЛайн"),
    ("severtrans", "Сергей Орлов", "carrier", "СеверТранс"),
    ("bystraya", "Ольга Белова", "carrier", "Быстрая Доставка"),
    ("operator", "Мария Волкова", "operator", "Банк"),
]
BUSINESS_TABLES = [
    "document_scans", "idempotency_keys", "decisions", "findings", "trip_events", "waybill_registry",
    "documents", "ledger_lines", "ledger_txns", "accounts", "orders",
]


def ensure_people(conn: Connection) -> None:
    if conn.execute(text("SELECT count(*) FROM companies")).scalar():
        return
    ids = {}
    for name, kind, _ in COMPANIES:
        ids[name] = conn.execute(text("INSERT INTO companies(name, kind) VALUES (:n, :k) RETURNING id"),
                                 {"n": name, "k": kind}).scalar_one()
    pw = hash_password(config.DEMO_PASSWORD)
    for login, full, role, company in USERS:
        conn.execute(text("INSERT INTO users(login, password_hash, full_name, role, company_id) VALUES (:l,:p,:f,:r,:c)"),
                     {"l": login, "p": pw, "f": full, "r": role, "c": ids[company]})


def reset_business_data(conn: Connection) -> None:
    # TRUNCATE — единственный путь очистить журналы «только на добавление»;
    # доступен лишь в демо-режиме и только оператору.
    conn.execute(text(f"TRUNCATE {', '.join(BUSINESS_TABLES)} RESTART IDENTITY CASCADE"))
    conn.execute(text("INSERT INTO accounts(company_id, kind) VALUES (NULL, 'funding')"))
    for name, kind, start in COMPANIES:
        cid = conn.execute(text("SELECT id FROM companies WHERE name=:n"), {"n": name}).scalar_one()
        for acc in ("available", "invoice"):
            conn.execute(text("INSERT INTO accounts(company_id, kind) VALUES (:c, :k)"), {"c": cid, "k": acc})
        if start:
            ledger.transfer(conn, "funding", f"Начальный остаток: {name}",
                            ledger.account_id(conn, None, "funding"),
                            ledger.account_id(conn, cid, "available"), start)


def seed(conn: Connection, reset: bool = False) -> None:
    ensure_people(conn)
    if reset or not conn.execute(text("SELECT count(*) FROM accounts")).scalar():
        reset_business_data(conn)
