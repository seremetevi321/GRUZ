"""Документы, загруженные фото или сканом: хранение оригинала, распознавание, подтверждение человеком."""
import hashlib
import json
import re

from sqlalchemy import text
from sqlalchemy.engine import Connection

from . import reconcile
from . import services as svc
from .errors import DomainError, not_found

DOC_STATUSES = ("accepted", "in_transit", "delivered", "price_proposed", "disputed", "blocked")

FIELD_TITLES = {
    "number": "Номер", "date": "Дата", "trip_sheet_number": "К путевому листу №", "driver_name": "Водитель",
    "driver_license": "Удостоверение", "vehicle_model": "Автомобиль", "vehicle_plate": "Госномер",
    "vehicle_type": "Тип ТС", "medical_check": "Медосмотр",
}


def check_can_upload(conn: Connection, user: dict, order_id: int) -> dict:
    svc.require_role(user, "carrier")
    o = svc.load_order(conn, user, order_id)
    svc.require_status(o, *DOC_STATUSES)
    return o


def save_scan(conn: Connection, user: dict, order_id: int, doc_type: str, data: bytes, analysis: dict) -> dict:
    o = conn.execute(text("SELECT * FROM orders WHERE id=:i FOR UPDATE"), {"i": order_id}).mappings().one()
    svc.require_status(dict(o), *DOC_STATUSES)
    sha = hashlib.sha256(data).hexdigest()
    # Тот же файл уже приносили к другой заявке — сигнал для оператора (попытка повторного финансирования).
    reused = conn.execute(text("SELECT DISTINCT order_id FROM document_scans WHERE sha256=:s AND order_id<>:o"),
                          {"s": sha, "o": order_id}).scalars().all()
    if reused:
        nums = ", ".join(f"№{x}" for x in reused)
        analysis["warnings"].append(f"Этот же файл уже загружался к заявке {nums}")
        svc.decide(conn, order_id, user["login"], "scan_reused",
                   f"Внимание: файл документа совпадает с файлом, загруженным к заявке {nums}")
    stored = {k: v for k, v in analysis.items() if k != "preview"}
    scan_id = conn.execute(text("""
        INSERT INTO document_scans(order_id, uploaded_by, doc_type, detected_type, mime, size_bytes, sha256, content, recognized)
        VALUES (:o, :u, :t, :dt, :m, :sz, :sha, :c, CAST(:r AS jsonb)) RETURNING id"""),
        {"o": order_id, "u": user["id"], "t": doc_type, "dt": analysis.get("detected_type"), "m": analysis["source_mime"],
         "sz": len(data), "sha": sha, "c": data, "r": json.dumps(stored, ensure_ascii=False)}).scalar_one()
    return {"scan_id": scan_id, **analysis}


def _same(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    na = re.sub(r"[^0-9A-ZА-ЯЁ]", "", str(a or "").upper()).translate(reconcile._LAT2CYR)
    nb = re.sub(r"[^0-9A-ZА-ЯЁ]", "", str(b or "").upper()).translate(reconcile._LAT2CYR)
    return na == nb


def submit(conn: Connection, user: dict, order_id: int, doc_type: str, data: dict, scan_id: int | None) -> dict:
    svc.require_role(user, "carrier")
    o = svc.load_order(conn, user, order_id, lock=True)
    svc.require_status(o, *DOC_STATUSES)
    note, scan = "", None
    if scan_id is not None:
        scan = conn.execute(text("SELECT id, doc_type, recognized, confirmed_at FROM document_scans "
                                 "WHERE id=:s AND order_id=:o FOR UPDATE"), {"s": scan_id, "o": order_id}).mappings().first()
        if not scan:
            raise not_found("Скан документа не найден")
        if scan["confirmed_at"] is not None:
            raise DomainError(409, "scan_used", "Этот скан уже подтверждён и подан")
        if scan["doc_type"] != doc_type:
            raise DomainError(422, "scan_type", "Тип документа не совпадает с распознанным сканом")
        rec = scan["recognized"]["fields"]
        filled = [k for k, f in rec.items() if f["value"] is not None]
        corrected = [k for k in data if k in rec and rec[k]["value"] is not None and not _same(rec[k]["value"], data[k])]
        added = [k for k in data if k in rec and rec[k]["value"] is None and data[k] not in (None, "")]
        note = f" по скану: распознано автоматически {len(filled)} из {len(rec)} полей"
        if corrected:
            note += ", исправлено вручную: " + ", ".join(FIELD_TITLES.get(k, k) for k in corrected)
        if added:
            note += ", дополнено вручную: " + ", ".join(FIELD_TITLES.get(k, k) for k in added)
        corrections = {k: {"recognized": rec[k]["value"], "confirmed": data[k]} for k in corrected + added}
    result = svc._submit_document(conn, user, o, doc_type, data, actor=user["login"], note=note)
    if scan is not None:
        conn.execute(text("""UPDATE document_scans SET confirmed=CAST(:c AS jsonb), corrections=CAST(:k AS jsonb),
                             confirmed_at=now(), document_id=:d WHERE id=:s"""),
                     {"c": json.dumps(data, ensure_ascii=False), "k": json.dumps(corrections, ensure_ascii=False),
                      "d": result.get("document_id"), "s": scan_id})
    return result


def file(conn: Connection, user: dict, scan_id: int) -> tuple[bytes, str]:
    row = conn.execute(text("SELECT order_id, content, mime FROM document_scans WHERE id=:s"), {"s": scan_id}).first()
    if not row:
        raise not_found("Скан не найден")
    svc.load_order(conn, user, row.order_id)  # чужой скан выглядит как несуществующий
    return bytes(row.content), row.mime


def list_for_order(conn: Connection, order_id: int) -> list[dict]:
    rows = conn.execute(text("""SELECT s.id, s.doc_type, s.mime, s.size_bytes, s.created_at, s.confirmed_at, s.corrections,
                                       u.login AS uploaded_by
                                FROM document_scans s JOIN users u ON u.id=s.uploaded_by
                                WHERE s.order_id=:o AND s.confirmed_at IS NOT NULL ORDER BY s.id"""), {"o": order_id}).mappings()
    return [{**dict(r), "created_at": r["created_at"].isoformat(), "confirmed_at": r["confirmed_at"].isoformat()} for r in rows]
