"""Нормализация и правила сверки на данных из образцов ТТН и путевого листа."""
from datetime import datetime, timedelta, timezone

from app import reconcile as r

ORDER = {"loading_norm_min": 120, "unloading_norm_min": 120, "downtime_rate_hour": 100_000}


def test_person_normalization():
    assert r.norm_person("Иванов Ю.И.") == r.norm_person("ИВАНОВ Ю. И.") == r.norm_person("Иванов Юрий Иванович")
    assert r.norm_person("Иванов Ю.И.") != r.norm_person("Иванов И.И.")
    assert r.norm_person("Иванов ИИ") == r.norm_person("Иванов И.И.")


def test_plate_normalization_latin_vs_cyrillic():
    assert r.norm_plate("X133MT77RUS") == r.norm_plate("Х 133 МТ 77") == "Х133МТ77"


def test_license_normalization():
    assert r.norm_license("57МК № 113540") == r.norm_license("57MK113540")


def _events(load_min=60):
    t0 = datetime(2026, 5, 21, 7, tzinfo=timezone.utc)
    m = lambda x: {"occurred_at": t0 + timedelta(minutes=x)}  # noqa: E731
    return {"departed": m(0), "arrived_loading": m(30), "departed_loading": m(30 + load_min),
            "arrived_unloading": m(300), "departed_unloading": m(360), "delivered": m(370)}


def test_sample_documents_produce_findings():
    """Пара из образцов: ТТН №512 (КамАЗ, Иванов Ю.И., к путевому листу №150) и
    путевой лист №329 (легковой Skoda, Иванов ИИ, медосмотр не отмечен)."""
    waybill = {"number": "512", "trip_sheet_number": "150", "driver_name": "Иванов Ю.И.",
               "driver_license": "57МК № 113540", "vehicle_plate": "X133MT77RUS"}
    sheet = {"number": "329", "driver_name": "Иванов ИИ", "vehicle_plate": "А 123 ВС 96",
             "vehicle_type": "car", "medical_check": False}
    rules = {f.rule for f in r.blocking(r.run(ORDER, waybill, sheet, _events()))}
    assert rules == {"driver_match", "vehicle_match", "vehicle_type", "trip_sheet_ref", "medical_check"}


def test_downtime_150_minutes_is_2500():
    f = r.run(ORDER, None, None, _events(load_min=270))
    assert r.downtime_total(f) == 250_000


def test_missing_events_block():
    ev = _events(); del ev["delivered"]
    assert any(f.rule == "events_complete" for f in r.blocking(r.run(ORDER, None, None, ev)))
