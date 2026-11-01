import hashlib
import json
import logging
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from . import config, ledger, scans, services as svc
from .db import read, tx
from .errors import DomainError, forbidden
from .migrate import migrate
from .ocr.pipeline import AnalysisError, analyze
from .schemas import DisputeIn, DocumentIn, EventIn, LoginIn, OrderIn, PriceIn, ResolveIn
from .security import login_limiter, new_token, token_hash, verify_password
from .seed import seed

log = logging.getLogger("escrow")


@asynccontextmanager
async def lifespan(_app):
    migrate()
    with tx() as conn:
        seed(conn)
    yield


app = FastAPI(lifespan=lifespan, title="Безопасная сделка: грузоперевозки", version="0.1.0",
              docs_url="/api/docs" if config.DEMO_MODE else None, openapi_url="/api/openapi.json" if config.DEMO_MODE else None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
)


@app.middleware("http")
async def hardening(request: Request, call_next):
    cl = request.headers.get("content-length")
    limit = config.SCAN_MAX_BYTES + 64 * 1024 if request.url.path.endswith("/scans") else config.MAX_BODY_BYTES
    if request.method == "POST" and request.url.path.endswith("/scans") and not cl:
        return JSONResponse({"error": "length_required", "message": "Нужен размер файла"}, status_code=411)
    if cl and (not cl.isdigit() or int(cl) > limit):
        return JSONResponse({"error": "too_large", "message": "Слишком большой запрос"}, status_code=413)
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.exception_handler(DomainError)
async def domain_error(_: Request, e: DomainError):
    return JSONResponse({"error": e.code, "message": e.message}, status_code=e.status)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, e: RequestValidationError):
    first = e.errors()[0] if e.errors() else {}
    field = ".".join(str(x) for x in first.get("loc", [])[1:])
    return JSONResponse({"error": "validation", "message": f"Некорректные данные: {field} — {first.get('msg', '')}"},
                        status_code=422)


@app.exception_handler(Exception)
async def unexpected(_: Request, e: Exception):
    log.exception("unhandled")  # подробности — только в лог сервера, не клиенту
    return JSONResponse({"error": "internal", "message": "Внутренняя ошибка. Данные не изменены."}, status_code=500)


# ---------- авторизация ----------

def current_user(authorization: str = Header(default="")) -> dict:
    if not authorization.startswith("Bearer "):
        raise DomainError(401, "unauthorized", "Требуется вход")
    with read() as conn:
        row = conn.execute(text("""
            SELECT u.id, u.login, u.full_name, u.role, u.company_id, c.name AS company_name
            FROM sessions s JOIN users u ON u.id=s.user_id JOIN companies c ON c.id=u.company_id
            WHERE s.token_hash=:h AND s.expires_at > now()"""), {"h": token_hash(authorization[7:])}).mappings().first()
    if not row:
        raise DomainError(401, "unauthorized", "Сессия истекла, войдите снова")
    return dict(row)


@app.post("/api/auth/login")
def login(body: LoginIn, request: Request):
    key = f"{body.login.lower()}|{request.client.host if request.client else '-'}"
    if not login_limiter.allow(key):
        raise DomainError(429, "too_many_attempts", "Слишком много попыток входа. Попробуйте через 5 минут.")
    with tx() as conn:
        u = conn.execute(text("SELECT id, password_hash FROM users WHERE login=:l"), {"l": body.login.lower()}).first()
        if not verify_password(u.password_hash if u else None, body.password):
            raise DomainError(401, "bad_credentials", "Неверный логин или пароль")
        token = new_token()
        conn.execute(text("DELETE FROM sessions WHERE expires_at < now()"))
        conn.execute(text("INSERT INTO sessions(token_hash, user_id, expires_at) VALUES (:h, :u, :e)"),
                     {"h": token_hash(token), "u": u.id,
                      "e": datetime.now(timezone.utc) + timedelta(hours=config.SESSION_TTL_HOURS)})
    login_limiter.reset(key)
    return {"token": token}


@app.post("/api/auth/logout")
def logout(authorization: str = Header(default="")):
    if authorization.startswith("Bearer "):
        with tx() as conn:
            conn.execute(text("DELETE FROM sessions WHERE token_hash=:h"), {"h": token_hash(authorization[7:])})
    return {"ok": True}


# ---------- идемпотентность ----------

def idempotent(user: dict, key: str | None, payload, op):
    """Повтор запроса с тем же ключом возвращает прежний ответ и ничего не меняет.
    Ключи одного пользователя сериализуются advisory-блокировкой."""
    if not key or len(key) > 100:
        raise DomainError(400, "idempotency_key_required", "Нужен заголовок Idempotency-Key (до 100 символов)")
    h = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    try:
        with tx() as conn:
            conn.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"{user['id']}:{key}"})
            prev = conn.execute(text("SELECT request_hash, status_code, response FROM idempotency_keys "
                                     "WHERE user_id=:u AND key=:k"), {"u": user["id"], "k": key}).first()
            if prev:
                if prev.request_hash != h:
                    raise DomainError(422, "idempotency_mismatch", "Этот ключ уже использован для другого запроса")
                return JSONResponse(prev.response, status_code=prev.status_code, headers={"Idempotent-Replay": "true"})
            result = op(conn)
            conn.execute(text("INSERT INTO idempotency_keys(user_id, key, request_hash, status_code, response) "
                              "VALUES (:u, :k, :h, 200, CAST(:r AS jsonb))"),
                         {"u": user["id"], "k": key, "h": h, "r": json.dumps(result, ensure_ascii=False, default=str)})
            return result
    except DomainError as e:
        # Отказ, о котором должна знать лента (например, нехватка средств), пишем отдельной транзакцией:
        # сама операция откатилась, а причина отказа сохранилась.
        if e.log and e.order_id:
            with tx() as conn:
                svc.decide(conn, e.order_id, user["login"], "refused", e.message)
        raise


IdemKey = Header(default=None, alias="Idempotency-Key")


# ---------- профиль и справочники ----------

@app.get("/api/me")
def me(user=Depends(current_user)):
    with read() as conn:
        return {**user, "balances": svc.balances(conn, user), "demo_mode": config.DEMO_MODE}


@app.get("/api/carriers")
def carriers(user=Depends(current_user)):
    with read() as conn:
        return [dict(r) for r in conn.execute(text("SELECT id, name FROM companies WHERE kind='carrier' ORDER BY id")).mappings()]


# ---------- заявки ----------

@app.get("/api/orders")
def orders(user=Depends(current_user)):
    with read() as conn:
        return svc.list_orders(conn, user)


@app.get("/api/orders/{order_id}")
def order(order_id: int, user=Depends(current_user)):
    with read() as conn:
        return svc.order_detail(conn, user, order_id)


@app.post("/api/orders")
def create(body: OrderIn, user=Depends(current_user), key: str | None = IdemKey):
    return idempotent(user, key, body.model_dump(), lambda c: svc.create_order(c, user, **body.model_dump()))


def _action(name, fn):
    def handler(order_id: int, user=Depends(current_user), key: str | None = IdemKey):
        return idempotent(user, key, {"a": name, "o": order_id}, lambda c: fn(c, user, order_id))
    app.post(f"/api/orders/{{order_id}}/{name}", name=name)(handler)


_action("reserve", svc.reserve)
_action("accept", svc.accept)
_action("confirm-price", svc.confirm_price)


@app.post("/api/orders/{order_id}/propose-price")
def propose(order_id: int, body: PriceIn, user=Depends(current_user), key: str | None = IdemKey):
    return idempotent(user, key, {"a": "propose", "o": order_id, **body.model_dump()},
                      lambda c: svc.propose_price(c, user, order_id, body.amount))


@app.post("/api/orders/{order_id}/dispute")
def dispute(order_id: int, body: DisputeIn, user=Depends(current_user), key: str | None = IdemKey):
    return idempotent(user, key, {"a": "dispute", "o": order_id, **body.model_dump()},
                      lambda c: svc.dispute(c, user, order_id, body.comment))


@app.post("/api/orders/{order_id}/resolve")
def resolve(order_id: int, body: ResolveIn, user=Depends(current_user), key: str | None = IdemKey):
    return idempotent(user, key, {"a": "resolve", "o": order_id, **body.model_dump()},
                      lambda c: svc.resolve_dispute(c, user, order_id, body.amount, body.comment))


# ---------- анализ документов: фото / скан / PDF → цифровой документ ----------

_scan_slots = threading.BoundedSemaphore(config.SCAN_CONCURRENCY)


@app.post("/api/orders/{order_id}/scans")
def upload_scan(order_id: int, file: UploadFile = File(...), doc_type: str | None = Form(default=None),
                user=Depends(current_user)):
    if doc_type not in (None, "waybill", "trip_sheet"):
        raise DomainError(422, "bad_doc_type", "Неизвестный тип документа")
    with read() as conn:
        scans.check_can_upload(conn, user, order_id)
    data = file.file.read(config.SCAN_MAX_BYTES + 1)
    if len(data) > config.SCAN_MAX_BYTES:
        raise DomainError(413, "too_large", "Файл больше 10 МБ")
    if not _scan_slots.acquire(timeout=30):
        raise DomainError(503, "busy", "Сервер распознавания занят, повторите через минуту")
    try:
        analysis = analyze(data, doc_type)
    except AnalysisError as e:
        raise DomainError(422, "unreadable", str(e))
    finally:
        _scan_slots.release()
    with tx() as conn:
        return scans.save_scan(conn, user, order_id, analysis["doc_type"], data, analysis)


@app.post("/api/orders/{order_id}/documents")
def submit_document(order_id: int, body: DocumentIn, user=Depends(current_user), key: str | None = IdemKey):
    doc = body.waybill if body.doc_type == "waybill" else body.trip_sheet
    if doc is None:
        raise DomainError(422, "no_document", "Нет данных документа")
    data = doc.model_dump()
    return idempotent(user, key, {"a": "doc", "o": order_id, **body.model_dump()},
                      lambda c: scans.submit(c, user, order_id, body.doc_type, data, body.scan_id))


@app.get("/api/scans/{scan_id}/file")
def scan_file(scan_id: int, user=Depends(current_user)):
    with read() as conn:
        content, mime = scans.file(conn, user, scan_id)
    ext = {"image/jpeg": "jpg", "image/png": "png", "application/pdf": "pdf"}[mime]
    return Response(content, media_type=mime, headers={
        "Content-Disposition": f'attachment; filename="scan-{scan_id}.{ext}"',
        "Content-Security-Policy": "default-src 'none'; sandbox"})


# ---------- симулятор внешних систем (только демо) ----------

@app.post("/api/orders/{order_id}/events")
def event(order_id: int, body: EventIn, user=Depends(current_user)):
    if not config.DEMO_MODE:
        raise forbidden("Симулятор выключен: события приходят от внешних систем")
    payload = {}
    if body.kind == "waybill_signed":
        if not body.waybill:
            raise DomainError(422, "no_document", "Для события «накладная подписана» нужны данные накладной")
        payload = body.waybill.model_dump()
    elif body.kind == "trip_sheet_issued":
        if not body.trip_sheet:
            raise DomainError(422, "no_document", "Для события «путевой лист выпущен» нужны данные путевого листа")
        payload = body.trip_sheet.model_dump()
    occurred = body.occurred_at if body.occurred_at.tzinfo else body.occurred_at.replace(tzinfo=timezone.utc)
    with tx() as conn:
        return svc.submit_event(conn, user, order_id, body.event_id, body.kind, occurred, payload)


# ---------- лента, панель оператора, сброс ----------

@app.get("/api/decisions")
def decisions(user=Depends(current_user)):
    with read() as conn:
        return svc.decisions(conn, user)


@app.get("/api/operator/metrics")
def metrics(user=Depends(current_user)):
    with read() as conn:
        return svc.metrics(conn, user)


@app.get("/api/operator/invariants")
def invariants(user=Depends(current_user)):
    svc.require_role(user, "operator")
    with read() as conn:
        return ledger.invariants(conn)


@app.post("/api/admin/reset")
def reset(user=Depends(current_user)):
    svc.require_role(user, "operator")
    if not config.DEMO_MODE:
        raise forbidden("Сброс доступен только в демо-режиме")
    with tx() as conn:
        seed(conn, reset=True)
        svc.decide(conn, None, user["login"], "reset", "Данные сброшены к исходному состоянию")
    return {"ok": True}


# ---------- интерфейс для режима «в браузере» ----------

_front = Path(os.environ.get("FRONTEND_DIR", Path(__file__).resolve().parents[2] / "frontend" / "dist"))
if _front.is_dir():
    app.mount("/assets", StaticFiles(directory=_front / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise DomainError(404, "not_found", "Не найдено")
        return FileResponse(_front / "index.html", headers={
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                       "img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"})
