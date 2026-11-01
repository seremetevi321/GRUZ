"""Модуль анализа документов: распознавание реальных бланков и сценарий загрузки."""
from pathlib import Path

import pytest

from app.ocr.pipeline import analyze
from .helpers import RUB, event, new_order

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
TTN = (SAMPLES / "ttn-512.jpg").read_bytes()
SHEET = (SAMPLES / "trip-sheet-329.jpg").read_bytes()


@pytest.fixture(scope="module")
def ttn_result():
    return analyze(TTN)


@pytest.fixture(scope="module")
def sheet_result():
    return analyze(SHEET)


def values(r):
    return {k: f["value"] for k, f in r["fields"].items()}


def test_waybill_photo_recognized(ttn_result):
    assert ttn_result["doc_type"] == "waybill"
    v = values(ttn_result)
    assert v["number"] == "512"
    assert v["driver_name"] == "Иванов Ю.И."
    assert v["driver_license"] == "57МК 113540"
    assert v["vehicle_plate"] == "Х133МТ77"
    assert v["vehicle_model"] == "КамАЗ 53-215"
    assert v["date"] == "21.05.2025"


def test_trip_sheet_scan_recognized(sheet_result):
    assert sheet_result["doc_type"] == "trip_sheet"
    v = values(sheet_result)
    assert v["number"] == "329"
    assert v["driver_name"] == "Иванов И.И."
    assert v["vehicle_plate"] == "А123ВС96"
    assert v["vehicle_model"] == "Skoda Octavia 1.6"
    assert v["vehicle_type"] == "car"
    assert v["medical_check"] is False          # в бланке вместо даты шаблон «ДД.ММ.ГГГГ»
    assert v["date"] == "13.01.2021"            # не дата приказа Минтранса из шапки
    assert v["driver_license"] is None          # поле в бланке пустое — не выдумываем


def test_every_found_field_has_box_and_status(ttn_result, sheet_result):
    for r in (ttn_result, sheet_result):
        assert r["preview"].startswith("data:image/jpeg;base64,")
        for f in r["fields"].values():
            assert f["status"] in ("ok", "check", "missing")
            if f["value"] is not None:
                x0, y0, x1, y1 = f["box"]
                assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1


# ---------- загрузка через API ----------

def upload(api, oid, data, name="doc.jpg", doc_type=None):
    files = {"file": (name, data, "application/octet-stream")}
    return api.c.post(f"/api/orders/{oid}/scans", files=files, headers=api.h,
                      data={"doc_type": doc_type} if doc_type else None)


def test_only_order_carrier_can_upload(alfa, carrier, other_carrier, operator):
    oid = new_order(alfa, carrier, 10_000)
    assert upload(alfa, oid, TTN).status_code == 403
    assert upload(other_carrier, oid, TTN).status_code == 404
    assert upload(operator, oid, TTN).status_code == 403


def test_rejects_non_documents(alfa, carrier):
    oid = new_order(alfa, carrier, 10_000)
    r = upload(carrier, oid, b"<script>alert(1)</script>", "x.jpg")
    assert r.status_code == 422 and "JPG" in r.json()["message"]
    r = upload(carrier, oid, b"\x89PNG\r\n\x1a\n" + b"\x00" * 50, "x.png")
    assert r.status_code == 422


def test_upload_confirm_and_reconcile(alfa, beta, carrier, operator):
    """Реальная «сломанная» пара из образцов: сверка находит все расхождения."""
    oid = new_order(alfa, carrier, 100_000)
    r = upload(carrier, oid, TTN, doc_type="waybill")
    assert r.status_code == 200, r.text
    scan = r.json()
    fields = {k: f["value"] for k, f in scan["fields"].items()}
    wb = {k: fields[k] for k in ("number", "date", "driver_name", "driver_license", "vehicle_plate", "vehicle_model")}
    wb["trip_sheet_number"] = "150"  # поле не распознано — перевозчик дописал вручную
    r = carrier.post(f"/api/orders/{oid}/documents", {"doc_type": "waybill", "scan_id": scan["scan_id"], "waybill": wb})
    assert r.status_code == 200, r.text
    msg = [d["message"] for d in carrier.ok("get", f"/api/orders/{oid}")["decisions"] if d["kind"] == "document"][0]
    assert "по скану: распознано автоматически" in msg and "дополнено вручную: К путевому листу №" in msg

    # тот же скан второй раз не подать
    r = carrier.post(f"/api/orders/{oid}/documents", {"doc_type": "waybill", "scan_id": scan["scan_id"], "waybill": wb})
    assert r.status_code == 409

    r = upload(carrier, oid, SHEET)
    assert r.status_code == 200 and r.json()["doc_type"] == "trip_sheet"
    s2 = r.json()
    ts = {k: f["value"] for k, f in s2["fields"].items() if k != "driver_license"}
    r = carrier.post(f"/api/orders/{oid}/documents", {"doc_type": "trip_sheet", "scan_id": s2["scan_id"], "trip_sheet": ts})
    assert r.status_code == 200, r.text

    for kind, at in [("departed", "2026-05-21T07:00:00Z"), ("arrived_loading", "2026-05-21T08:00:00Z"),
                     ("departed_loading", "2026-05-21T09:00:00Z"), ("arrived_unloading", "2026-05-21T11:00:00Z"),
                     ("departed_unloading", "2026-05-21T12:00:00Z"), ("delivered", "2026-05-21T12:05:00Z")]:
        assert event(carrier, oid, kind, at).status_code == 200
    carrier.ok("post", f"/api/orders/{oid}/propose-price", {"amount": 100_000 * RUB})
    r = alfa.ok("post", f"/api/orders/{oid}/confirm-price")
    assert r["status"] == "blocked"
    rules = {f["rule"] for f in operator.ok("get", f"/api/orders/{oid}")["findings"] if f["severity"] == "block"}
    assert {"driver_match", "vehicle_match", "vehicle_type", "trip_sheet_ref", "medical_check"} <= rules

    # оригинал доступен участникам заявки и скрыт от чужих
    assert alfa.get(f"/api/scans/{scan['scan_id']}/file").status_code == 200
    assert beta.get(f"/api/scans/{scan['scan_id']}/file").status_code == 404
    assert len(operator.ok("get", f"/api/orders/{oid}")["scans"]) == 2


def test_same_file_on_another_order_is_flagged(alfa, carrier, operator):
    a = new_order(alfa, carrier, 10_000)
    b = new_order(alfa, carrier, 10_000)
    assert upload(carrier, a, SHEET).status_code == 200
    r = upload(carrier, b, SHEET)
    assert any("уже загружался к заявке №" in w for w in r.json()["warnings"])
    assert any(d["kind"] == "scan_reused" for d in operator.ok("get", "/api/decisions"))


def test_clean_pair_from_photos_pays_out(alfa, carrier, operator):
    """Демо-пара: фото ТТН (наклон, шум) и скан путевого листа сходятся → выплата без участия человека."""
    oid = new_order(alfa, carrier, 120_000)
    for name, kind in [("ttn-1001-photo.jpg", "waybill"), ("trip-sheet-1001-scan.png", "trip_sheet")]:
        r = upload(carrier, oid, (SAMPLES / name).read_bytes())
        assert r.status_code == 200, r.text
        scan = r.json()
        assert scan["doc_type"] == kind
        assert all(f["status"] != "missing" for k, f in scan["fields"].items() if k != "trip_sheet_number" or kind == "waybill")
        data = {k: f["value"] for k, f in scan["fields"].items()}
        r = carrier.post(f"/api/orders/{oid}/documents", {"doc_type": kind, "scan_id": scan["scan_id"], kind: data})
        assert r.status_code == 200, r.text
    for k, at in [("departed", "2026-10-18T07:00:00Z"), ("arrived_loading", "2026-10-18T08:00:00Z"),
                  ("departed_loading", "2026-10-18T09:00:00Z"), ("arrived_unloading", "2026-10-18T11:00:00Z"),
                  ("departed_unloading", "2026-10-18T12:00:00Z"), ("delivered", "2026-10-18T12:05:00Z")]:
        assert event(carrier, oid, k, at).status_code == 200
    carrier.ok("post", f"/api/orders/{oid}/propose-price", {"amount": 120_000 * RUB})
    r = alfa.ok("post", f"/api/orders/{oid}/confirm-price")
    assert r["status"] == "paid" and r["paid"] == 120_000 * RUB
    msgs = [d["message"] for d in operator.ok("get", f"/api/orders/{oid}")["decisions"] if d["kind"] == "document"]
    assert all("распознано автоматически" in m and "исправлено" not in m for m in msgs)


def test_pdf_scan_recognized():
    r = analyze((SAMPLES / "trip-sheet-1001.pdf").read_bytes())
    assert r["doc_type"] == "trip_sheet" and r["source_mime"] == "application/pdf"
    v = values(r)
    assert v["number"] == "1001" and v["driver_name"] == "Смирнов А.В." and v["vehicle_plate"] == "К456ОР199"
