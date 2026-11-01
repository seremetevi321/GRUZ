"""Бизнес-операции. Каждая функция работает внутри одной транзакции и
сама проверяет роль, владение заявкой и допустимость перехода состояния.
Клиенту не доверяем: всё, что пришло из интерфейса, перепроверяется здесь."""
import json
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Connection

from . import ledger, reconcile
from .errors import DomainError, conflict, forbidden, not_found
from .money import percent, rub

BUFFER_PCT = 10

STATUS_TITLES = {
    "created": "Создана",
    "reserved": "Деньги зарезервированы",
    "accepted": "Принята перевозчиком",
    "in_transit": "В пути",
    "delivered": "Доставлена",
    "price_proposed": "Стоимость предложена",
    "disputed": "Спор",
    "blocked": "Платёж заблокирован",
    "paid": "Выплачено, закрыта",
    "cancelled": "Отменена",
    "rejected": "Отклонена",
}

EVENT_TITLES = {"waybill_signed": "накладная подписана", "trip_sheet_issued": "путевой лист выпущен",
                **reconcile.REQUIRED_EVENTS}
EVENT_KINDS = set(EVENT_TITLES)


# ---------- общие помощники ----------

def now() -> datetime:
    return datetime.now(timezone.utc)


def decide(conn: Connection, order_id: int | None, actor: str, kind: str, message: str, amount: int | None = None):
    conn.execute(
        text("INSERT INTO decisions(order_id, actor, kind, amount, message) VALUES (:o, :a, :k, :x, :m)"),
        {"o": order_id, "a": actor, "k": kind, "x": amount, "m": message},
    )


def can_see(user: dict, order: dict) -> bool:
    if user["role"] == "operator":
        return True
    if user["role"] == "customer":
        return order["customer_id"] == user["company_id"]
    if user["role"] == "carrier":
        return order["carrier_id"] == user["company_id"]
    return False


def load_order(conn: Connection, user: dict, order_id: int, lock: bool = False) -> dict:
    sql = "SELECT * FROM orders WHERE id=:i" + (" FOR UPDATE" if lock else "")
    row = conn.execute(text(sql), {"i": order_id}).mappings().first()
    # Чужая заявка выглядит как несуществующая: не раскрываем сам факт её наличия.
    if not row or not can_see(user, row):
        raise not_found("Заявка не найдена")
    return dict(row)


def require_role(user: dict, *roles: str):
    if user["role"] not in roles:
        raise forbidden("Это действие недоступно для вашей роли")


def require_status(order: dict, *statuses: str):
    if order["status"] not in statuses:
        raise conflict(
            "bad_state",
            f"Действие недоступно: заявка в статусе «{STATUS_TITLES[order['status']]}»",
        )


def set_status(conn: Connection, order_id: int, status: str, **fields):
    sets = ", ".join([f"{k}=:{k}" for k in fields] + ["status=:status", "updated_at=now()"])
    conn.execute(text(f"UPDATE orders SET {sets} WHERE id=:id"), {"id": order_id, "status": status, **fields})


# ---------- заявки ----------

def create_order(conn: Connection, user: dict, carrier_id: int, route_from: str, route_to: str, cargo: str, price: int) -> dict:
    require_role(user, "customer")
    carrier = conn.execute(
        text("SELECT id, name FROM companies WHERE id=:i AND kind='carrier'"), {"i": carrier_id}
    ).first()
    if not carrier:
        raise DomainError(422, "bad_carrier", "Перевозчик не найден")
    buffer = percent(price, BUFFER_PCT)
    oid = conn.execute(
        text(
            """INSERT INTO orders(customer_id, carrier_id, route_from, route_to, cargo, price, buffer, status)
               VALUES (:c, :k, :f, :t, :g, :p, :b, 'created') RETURNING id"""
        ),
        {"c": user["company_id"], "k": carrier_id, "f": route_from, "t": route_to, "g": cargo, "p": price, "b": buffer},
    ).scalar_one()
    decide(conn, oid, user["login"], "created",
           f"Заявка создана: {route_from} → {route_to}, перевозчик {carrier.name}. "
           f"Цена {rub(price)}, к резервированию {rub(price + buffer)} (цена + {BUFFER_PCT}% на возможный простой).",
           price + buffer)
    return {"id": oid}


def reserve(conn: Connection, user: dict, order_id: int) -> dict:
    require_role(user, "customer")
    o = load_order(conn, user, order_id, lock=True)
    require_status(o, "created")
    total = o["price"] + o["buffer"]
    avail = ledger.account_id(conn, o["customer_id"], "available")
    res = ledger.reserve_account(conn, o["id"], o["customer_id"], create=True)
    have = ledger.lock(conn, [avail])[avail]
    if have < total:
        raise conflict(
            "insufficient_funds",
            f"Недостаточно средств для резерва: нужно {rub(total)}, доступно {rub(have)}. "
            f"Не хватает {rub(total - have)}.",
            order_id=o["id"], log=True,
        )
    ledger.transfer(conn, "reserve", f"Резерв по заявке №{o['id']}", avail, res, total, o["id"])
    set_status(conn, o["id"], "reserved")
    decide(conn, o["id"], user["login"], "reserved",
           f"Зарезервировано {rub(total)}: цена {rub(o['price'])} + {rub(o['buffer'])} на возможный простой. "
           "Перевозчик видит гарантию оплаты.", total)
    return {"status": "reserved"}


def accept(conn: Connection, user: dict, order_id: int) -> dict:
    require_role(user, "carrier")
    o = load_order(conn, user, order_id, lock=True)
    require_status(o, "reserved")
    set_status(conn, o["id"], "accepted")
    decide(conn, o["id"], user["login"], "accepted",
           f"Перевозчик принял заявку. Оплата гарантирована резервом {rub(o['price'] + o['buffer'])}.")
    return {"status": "accepted"}


# ---------- события симулятора (ЭДО, путевые листы, трек) ----------

def _latest_doc(conn: Connection, order_id: int, doc_type: str) -> dict | None:
    row = conn.execute(
        text("SELECT data, number, version FROM documents WHERE order_id=:o AND doc_type=:t ORDER BY version DESC LIMIT 1"),
        {"o": order_id, "t": doc_type},
    ).first()
    return {**row.data, "number": row.number, "_version": row.version} if row else None


def _events(conn: Connection, order_id: int) -> dict[str, dict]:
    rows = conn.execute(
        text("SELECT kind, occurred_at, payload FROM trip_events WHERE order_id=:o ORDER BY occurred_at, id"),
        {"o": order_id},
    ).mappings().all()
    out: dict[str, dict] = {}
    for r in rows:
        out[r["kind"]] = dict(r)  # для повторяющегося вида актуально последнее
    return out


def submit_event(conn: Connection, user: dict, order_id: int, event_id: str, kind: str,
                 occurred_at: datetime, payload: dict) -> dict:
    if kind not in EVENT_KINDS:
        raise DomainError(422, "bad_event", "Неизвестный тип события")
    o = load_order(conn, user, order_id, lock=True)

    # Идемпотентность источника: то же event_id второй раз ничего не меняет.
    inserted = conn.execute(
        text(
            """INSERT INTO trip_events(event_id, order_id, kind, occurred_at, payload)
               VALUES (:e, :o, :k, :t, CAST(:p AS jsonb)) ON CONFLICT (event_id) DO NOTHING RETURNING id"""
        ),
        {"e": event_id, "o": o["id"], "k": kind, "t": occurred_at, "p": json.dumps(payload, ensure_ascii=False)},
    ).first()
    if not inserted:
        prev = conn.execute(text("SELECT order_id, kind FROM trip_events WHERE event_id=:e"), {"e": event_id}).first()
        if prev.order_id != o["id"] or prev.kind != kind:
            raise conflict("event_id_reused", "Этот идентификатор события уже использован для другого события")
        return {"duplicate": True, "status": o["status"]}

    require_status(o, "accepted", "in_transit", "delivered", "price_proposed", "disputed", "blocked")

    if kind in ("waybill_signed", "trip_sheet_issued"):
        return _submit_document(conn, user, o, "waybill" if kind == "waybill_signed" else "trip_sheet", payload)

    titles = reconcile.REQUIRED_EVENTS
    if kind == "departed" and o["status"] == "accepted":
        set_status(conn, o["id"], "in_transit")
    if kind == "delivered" and o["status"] in ("accepted", "in_transit"):
        set_status(conn, o["id"], "delivered")
    decide(conn, o["id"], "simulator", "event", f"Событие: {titles[kind]}")
    return {"status": load_order(conn, user, order_id)["status"]}


def _submit_document(conn: Connection, user: dict, o: dict, doc_type: str, data: dict,
                     actor: str = "simulator", note: str = "") -> dict:
    number = str(data.get("number", "")).strip()
    if not number:
        raise DomainError(422, "no_number", "В документе нет номера")
    title = reconcile.DOC_TITLES[doc_type]

    if doc_type == "waybill":
        # Повторное финансирование: номер накладной закрепляется за одной заявкой навсегда.
        owner = conn.execute(
            text("INSERT INTO waybill_registry(number, order_id) VALUES (:n, :o) "
                 "ON CONFLICT (number) DO NOTHING RETURNING order_id"),
            {"n": number, "o": o["id"]},
        ).first()
        if not owner:
            first = conn.execute(text("SELECT order_id FROM waybill_registry WHERE number=:n"), {"n": number}).scalar_one()
            if first != o["id"]:
                return _reject_duplicate(conn, o, number, first)

    version = conn.execute(
        text("SELECT COALESCE(MAX(version),0)+1 FROM documents WHERE order_id=:o AND doc_type=:t"),
        {"o": o["id"], "t": doc_type},
    ).scalar_one()
    clean = {k: v for k, v in data.items() if k != "number"}
    doc_id = conn.execute(
        text("INSERT INTO documents(order_id, doc_type, number, version, data) "
             "VALUES (:o, :t, :n, :v, CAST(:d AS jsonb)) RETURNING id"),
        {"o": o["id"], "t": doc_type, "n": number, "v": version, "d": json.dumps(clean, ensure_ascii=False)},
    ).scalar_one()
    what = "подан" if version == 1 else f"подан исправленный (версия {version})"
    decide(conn, o["id"], actor, "document", f"{title} №{number} {what}{note}")

    # Исправленный документ по заблокированной заявке — сверка заново и попытка выплаты.
    if o["status"] == "blocked":
        return {**try_payout(conn, o["id"], actor="system"), "document_id": doc_id}
    return {"status": o["status"], "document_id": doc_id}


def _reject_duplicate(conn: Connection, o: dict, number: str, first_order: int) -> dict:
    res = ledger.reserve_account(conn, o["id"], o["customer_id"])
    avail = ledger.account_id(conn, o["customer_id"], "available")
    amount = ledger.lock(conn, [res])[res]
    ledger.transfer(conn, "refund", f"Возврат резерва: заявка №{o['id']} отклонена как дубль", res, avail, amount, o["id"])
    set_status(conn, o["id"], "rejected", is_duplicate=True)
    decide(conn, o["id"], "system", "duplicate",
           f"Отклонено: накладная №{number} уже используется в заявке №{first_order}. "
           f"Флаг «дубль», платёж заблокирован. Резерв {rub(amount)} возвращён заказчику. "
           f"Заявка №{first_order} не затронута.", amount)
    return {"status": "rejected", "duplicate": True}


# ---------- сверка ----------

def run_reconciliation(conn: Connection, order_id: int) -> list[reconcile.Finding]:
    o = conn.execute(text("SELECT * FROM orders WHERE id=:i"), {"i": order_id}).mappings().one()
    findings = reconcile.run(dict(o), _latest_doc(conn, order_id, "waybill"),
                             _latest_doc(conn, order_id, "trip_sheet"), _events(conn, order_id))
    run_no = conn.execute(
        text("SELECT COALESCE(MAX(run_no),0)+1 FROM findings WHERE order_id=:o"), {"o": order_id}
    ).scalar_one()
    conn.execute(
        text("""INSERT INTO findings(order_id, run_no, rule, severity, document, field, message, guilty, amount)
                VALUES (:o, :r, :rule, :severity, :document, :field, :message, :guilty, :amount)"""),
        [{"o": order_id, "r": run_no, **f.dict()} for f in findings],
    )
    conn.execute(text("UPDATE orders SET downtime_comp=:d, updated_at=now() WHERE id=:i"),
                 {"d": reconcile.downtime_total(findings), "i": order_id})
    return findings


# ---------- стоимость и спор ----------

def propose_price(conn: Connection, user: dict, order_id: int, amount: int) -> dict:
    require_role(user, "carrier")
    o = load_order(conn, user, order_id, lock=True)
    require_status(o, "delivered")
    if amount > o["price"] + o["buffer"]:
        raise DomainError(422, "price_too_high",
                          f"Стоимость не может превышать резерв {rub(o['price'] + o['buffer'])}")
    findings = run_reconciliation(conn, o["id"])
    dt = reconcile.downtime_total(findings)
    set_status(conn, o["id"], "price_proposed", proposed_price=amount)
    extra = f" Компенсация простоя: +{rub(dt)}. Итого к выплате {rub(amount + dt)}." if dt else ""
    decide(conn, o["id"], user["login"], "price_proposed",
           f"Перевозчик предложил стоимость {rub(amount)} (в заявке {rub(o['price'])}).{extra}", amount)
    return {"status": "price_proposed"}


def confirm_price(conn: Connection, user: dict, order_id: int) -> dict:
    require_role(user, "customer")
    o = load_order(conn, user, order_id, lock=True)
    require_status(o, "price_proposed")
    set_status(conn, o["id"], "price_proposed", agreed_price=o["proposed_price"], price_agreed_at=now())
    decide(conn, o["id"], user["login"], "price_agreed",
           f"Заказчик подтвердил стоимость {rub(o['proposed_price'])}", o["proposed_price"])
    return try_payout(conn, o["id"], actor="system")


def dispute(conn: Connection, user: dict, order_id: int, comment: str) -> dict:
    require_role(user, "customer")
    o = load_order(conn, user, order_id, lock=True)
    require_status(o, "price_proposed")
    set_status(conn, o["id"], "disputed")
    decide(conn, o["id"], user["login"], "disputed",
           f"Заказчик оспорил стоимость {rub(o['proposed_price'])} (в заявке {rub(o['price'])}): «{comment}». "
           "Деньги остаются в резерве до решения оператора.", o["proposed_price"])
    return {"status": "disputed"}


def resolve_dispute(conn: Connection, user: dict, order_id: int, amount: int, comment: str) -> dict:
    require_role(user, "operator")
    o = load_order(conn, user, order_id, lock=True)
    require_status(o, "disputed")
    if amount > o["price"] + o["buffer"]:
        raise DomainError(422, "price_too_high", f"Сумма не может превышать резерв {rub(o['price'] + o['buffer'])}")
    set_status(conn, o["id"], "disputed", agreed_price=amount, price_agreed_at=now())
    decide(conn, o["id"], user["login"], "dispute_resolved",
           f"Оператор решил спор: стоимость {rub(amount)} (заказчик — {rub(o['price'])}, "
           f"перевозчик — {rub(o['proposed_price'])}). Комментарий: «{comment}»", amount)
    return try_payout(conn, o["id"], actor="system")


# ---------- выплата ----------

def try_payout(conn: Connection, order_id: int, actor: str) -> dict:
    o = conn.execute(text("SELECT * FROM orders WHERE id=:i FOR UPDATE"), {"i": order_id}).mappings().one()
    if o["agreed_price"] is None:
        raise conflict("not_agreed", "Стоимость ещё не согласована")
    findings = run_reconciliation(conn, order_id)
    blocks = reconcile.blocking(findings)
    if blocks:
        set_status(conn, order_id, "blocked")
        for f in blocks:
            decide(conn, order_id, actor, "payout_blocked", f"Платёж заблокирован. {f.message}", o["agreed_price"])
        return {"status": "blocked", "reasons": [f.message for f in blocks]}

    dt = reconcile.downtime_total(findings)
    payout = o["agreed_price"] + dt
    res = ledger.reserve_account(conn, order_id, o["customer_id"])
    cust_avail = ledger.account_id(conn, o["customer_id"], "available")
    carr_avail = ledger.account_id(conn, o["carrier_id"], "available")
    reserved = ledger.lock(conn, [res])[res]

    from_reserve = min(payout, reserved)
    excess = payout - from_reserve          # сверх резерва — отдельным счётом заказчику
    refund = reserved - from_reserve
    lines = [(res, -reserved), (carr_avail, from_reserve), (cust_avail, refund)]
    if excess:
        lines += [(ledger.account_id(conn, o["customer_id"], "invoice"), -excess),
                  (ledger.account_id(conn, o["carrier_id"], "invoice"), excess)]
    ledger.post(conn, "payout", f"Выплата по заявке №{order_id}", lines, order_id)
    set_status(conn, order_id, "paid", paid_at=now())

    parts = [f"{rub(o['agreed_price'])} за рейс"]
    if dt:
        parts.append(f"{rub(dt)} компенсация простоя")
    why = "рейс подтверждён, стоимость согласована" if not dt else "рейс подтверждён, стоимость и простой согласованы"
    breakdown = f" ({' + '.join(parts)})" if dt else ""
    decide(conn, order_id, actor, "paid", f"Выплачено {rub(from_reserve)}{breakdown}: {why}.", from_reserve)
    if excess:
        decide(conn, order_id, actor, "invoiced",
               f"Сумма сверх резерва {rub(excess)} выставлена заказчику отдельным счётом.", excess)
    if refund:
        decide(conn, order_id, actor, "refund", f"Заказчику возвращено {rub(refund)} неиспользованного резерва.", refund)
    return {"status": "paid", "paid": from_reserve, "refund": refund, "invoiced": excess}


# ---------- чтение ----------

def visible_orders_sql(user: dict) -> tuple[str, dict]:
    if user["role"] == "customer":
        return "o.customer_id = :c", {"c": user["company_id"]}
    if user["role"] == "carrier":
        return "o.carrier_id = :c", {"c": user["company_id"]}
    return "TRUE", {}


def list_orders(conn: Connection, user: dict) -> list[dict]:
    where, p = visible_orders_sql(user)
    rows = conn.execute(text(f"""
        SELECT o.*, c.name AS customer_name, k.name AS carrier_name
        FROM orders o JOIN companies c ON c.id=o.customer_id JOIN companies k ON k.id=o.carrier_id
        WHERE {where} ORDER BY o.id DESC"""), p).mappings().all()
    return [_order_out(dict(r)) for r in rows]


def _order_out(o: dict) -> dict:
    o["status_title"] = STATUS_TITLES[o["status"]]
    o["reserve_total"] = o["price"] + o["buffer"]
    for k in ("created_at", "updated_at", "price_agreed_at", "paid_at"):
        if o.get(k):
            o[k] = o[k].isoformat()
    return o


def order_detail(conn: Connection, user: dict, order_id: int) -> dict:
    load_order(conn, user, order_id)
    o = conn.execute(text("""
        SELECT o.*, c.name AS customer_name, k.name AS carrier_name
        FROM orders o JOIN companies c ON c.id=o.customer_id JOIN companies k ON k.id=o.carrier_id
        WHERE o.id=:i"""), {"i": order_id}).mappings().one()
    out = _order_out(dict(o))
    res = conn.execute(text("SELECT balance FROM accounts WHERE order_id=:i AND kind='reserve'"), {"i": order_id}).scalar()
    out["reserve_balance"] = res or 0
    out["documents"] = {t: _latest_doc(conn, order_id, t) for t in ("waybill", "trip_sheet")}
    out["events"] = [
        {"kind": r["kind"], "title": EVENT_TITLES[r["kind"]],
         "occurred_at": r["occurred_at"].isoformat()}
        for r in conn.execute(text("SELECT kind, occurred_at FROM trip_events WHERE order_id=:i ORDER BY occurred_at, id"),
                              {"i": order_id}).mappings()
    ]
    run_no = conn.execute(text("SELECT MAX(run_no) FROM findings WHERE order_id=:i"), {"i": order_id}).scalar()
    out["findings"] = [dict(r) for r in conn.execute(text(
        "SELECT rule, severity, document, field, message, guilty, amount FROM findings "
        "WHERE order_id=:i AND run_no=:r ORDER BY id"), {"i": order_id, "r": run_no}).mappings()] if run_no else []
    out["decisions"] = decisions(conn, user, order_id)
    from .scans import list_for_order
    out["scans"] = list_for_order(conn, order_id)
    return out


def decisions(conn: Connection, user: dict, order_id: int | None = None, limit: int = 200) -> list[dict]:
    where, p = visible_orders_sql(user)
    cond = f"(d.order_id IS NULL AND :op) OR EXISTS (SELECT 1 FROM orders o WHERE o.id=d.order_id AND {where})"
    p["op"] = user["role"] == "operator"
    if order_id is not None:
        cond = f"d.order_id = :oid AND ({cond})"
        p["oid"] = order_id
    rows = conn.execute(text(f"""SELECT d.id, d.order_id, d.actor, d.kind, d.amount, d.message, d.created_at
                                 FROM decisions d WHERE {cond} ORDER BY d.id DESC LIMIT :lim"""),
                        {**p, "lim": limit}).mappings().all()
    return [{**dict(r), "created_at": r["created_at"].isoformat()} for r in rows]


def balances(conn: Connection, user: dict) -> dict:
    cid = user["company_id"]
    avail = conn.execute(text("SELECT balance FROM accounts WHERE company_id=:c AND kind='available' AND order_id IS NULL"),
                         {"c": cid}).scalar() or 0
    reserved = conn.execute(text("SELECT COALESCE(SUM(balance),0) FROM accounts WHERE company_id=:c AND kind='reserve'"),
                            {"c": cid}).scalar()
    invoice = conn.execute(text("SELECT balance FROM accounts WHERE company_id=:c AND kind='invoice' AND order_id IS NULL"),
                           {"c": cid}).scalar() or 0
    return {"available": int(avail), "reserved": int(reserved), "invoice": int(invoice)}


def metrics(conn: Connection, user: dict) -> dict:
    require_role(user, "operator")
    q = lambda sql: conn.execute(text(sql)).scalar()  # noqa: E731
    reached_delivery = q("SELECT count(*) FROM orders WHERE status IN ('delivered','price_proposed','disputed','blocked','paid')")
    paid = q("SELECT count(*) FROM orders WHERE status='paid'")
    blocked_orders = q("SELECT count(DISTINCT order_id) FROM decisions WHERE kind='payout_blocked'")
    blocked_sum = q("""SELECT COALESCE(SUM(agreed_price),0) FROM orders WHERE id IN
                       (SELECT DISTINCT order_id FROM decisions WHERE kind='payout_blocked')""")
    reached_price = q("SELECT count(DISTINCT order_id) FROM decisions WHERE kind='price_proposed'")
    disputes = q("SELECT count(DISTINCT order_id) FROM decisions WHERE kind='disputed'")
    downtime = q("SELECT COALESCE(SUM(downtime_comp),0) FROM orders WHERE status='paid'")
    dups = q("SELECT count(*) FROM orders WHERE is_duplicate")
    avg_s = q("SELECT AVG(EXTRACT(EPOCH FROM paid_at - price_agreed_at)) FROM orders WHERE status='paid'")
    return {
        "avg_agree_to_pay_seconds": round(float(avg_s), 3) if avg_s is not None else None,
        "confirmed_share": round(paid / reached_delivery, 4) if reached_delivery else None,
        "confirmed": int(paid), "reached_delivery": int(reached_delivery),
        "blocked_count": int(blocked_orders), "blocked_sum": int(blocked_sum),
        "dispute_share": round(disputes / reached_price, 4) if reached_price else None,
        "disputes": int(disputes),
        "downtime_comp_sum": int(downtime),
        "duplicates": int(dups),
    }
