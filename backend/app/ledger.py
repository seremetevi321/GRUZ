"""Леджер с двойной записью.

Любое движение денег — одна проводка из нескольких строк, сумма строк равна нулю.
База дополнительно проверяет баланс проводки при COMMIT и не даёт счетам
available/reserve уйти в минус.
"""
from sqlalchemy import text
from sqlalchemy.engine import Connection

from .errors import DomainError


def account_id(conn: Connection, company_id: int | None, kind: str) -> int:
    if kind == "funding":
        row = conn.execute(text("SELECT id FROM accounts WHERE kind='funding'")).first()
    else:
        row = conn.execute(
            text("SELECT id FROM accounts WHERE company_id=:c AND kind=:k AND order_id IS NULL"),
            {"c": company_id, "k": kind},
        ).first()
    if not row:
        raise DomainError(500, "no_account", f"Нет счёта {kind} для компании {company_id}")
    return row[0]


def reserve_account(conn: Connection, order_id: int, customer_id: int, create: bool = False) -> int:
    row = conn.execute(
        text("SELECT id FROM accounts WHERE order_id=:o AND kind='reserve'"), {"o": order_id}
    ).first()
    if row:
        return row[0]
    if not create:
        raise DomainError(500, "no_reserve", f"У заявки {order_id} нет резерва")
    return conn.execute(
        text("INSERT INTO accounts(company_id, order_id, kind) VALUES (:c, :o, 'reserve') RETURNING id"),
        {"c": customer_id, "o": order_id},
    ).scalar_one()


def lock(conn: Connection, ids: list[int]) -> dict[int, int]:
    """Блокирует счета в порядке id (без взаимных блокировок) и возвращает их балансы."""
    rows = conn.execute(
        text("SELECT id, balance FROM accounts WHERE id = ANY(:ids) ORDER BY id FOR UPDATE"),
        {"ids": sorted(set(ids))},
    ).all()
    return {r[0]: r[1] for r in rows}


def balance(conn: Connection, acc_id: int) -> int:
    return conn.execute(text("SELECT balance FROM accounts WHERE id=:i"), {"i": acc_id}).scalar_one()


def post(conn: Connection, kind: str, memo: str, lines: list[tuple[int, int]], order_id: int | None = None) -> int:
    lines = [(a, amt) for a, amt in lines if amt != 0]
    if not lines:
        return 0
    if sum(amt for _, amt in lines) != 0:
        raise DomainError(500, "unbalanced", "Внутренняя ошибка: проводка не сходится")
    lock(conn, [a for a, _ in lines])
    txn = conn.execute(
        text("INSERT INTO ledger_txns(order_id, kind, memo) VALUES (:o, :k, :m) RETURNING id"),
        {"o": order_id, "k": kind, "m": memo},
    ).scalar_one()
    conn.execute(
        text("INSERT INTO ledger_lines(txn_id, account_id, amount) VALUES (:t, :a, :x)"),
        [{"t": txn, "a": a, "x": amt} for a, amt in lines],
    )
    return txn


def transfer(conn: Connection, kind: str, memo: str, src: int, dst: int, amount: int, order_id: int | None = None) -> int:
    if amount < 0:
        raise DomainError(500, "negative_transfer", "Внутренняя ошибка: отрицательный перевод")
    return post(conn, kind, memo, [(src, -amount), (dst, amount)], order_id)


def invariants(conn: Connection) -> dict:
    total = conn.execute(text("SELECT COALESCE(SUM(balance),0) FROM accounts")).scalar_one()
    lines_total = conn.execute(text("SELECT COALESCE(SUM(amount),0) FROM ledger_lines")).scalar_one()
    unbalanced = conn.execute(
        text("SELECT count(*) FROM (SELECT txn_id FROM ledger_lines GROUP BY txn_id HAVING SUM(amount) <> 0) t")
    ).scalar_one()
    drift = conn.execute(
        text(
            """SELECT count(*) FROM accounts a
               LEFT JOIN (SELECT account_id, SUM(amount) s FROM ledger_lines GROUP BY account_id) l
                 ON l.account_id = a.id
               WHERE a.balance <> COALESCE(l.s, 0)"""
        )
    ).scalar_one()
    client_money = conn.execute(
        text("SELECT COALESCE(SUM(balance),0) FROM accounts WHERE kind <> 'funding'")
    ).scalar_one()
    funded = -conn.execute(text("SELECT balance FROM accounts WHERE kind='funding'")).scalar_one()
    return {
        "sum_all_accounts": int(total),
        "sum_all_lines": int(lines_total),
        "unbalanced_txns": int(unbalanced),
        "accounts_out_of_sync": int(drift),
        "client_money": int(client_money),
        "funded": int(funded),
        "ok": total == 0 and lines_total == 0 and unbalanced == 0 and drift == 0 and client_money == funded,
    }
