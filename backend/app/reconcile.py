"""Движок сверки: чистые функции без доступа к БД.

Каждое правило возвращает находку: где ошибка, что не так, кто виноват и сколько.
Перед сравнением данные нормализуются, иначе «Иванов Ю.И.» и «Иванов Ю. И.»
или госномер латиницей и кириллицей дадут ложную блокировку.
"""
import re
from dataclasses import dataclass, asdict
from datetime import datetime

from .money import rub

REQUIRED_EVENTS = {
    "departed": "выезд",
    "arrived_loading": "прибытие на погрузку",
    "departed_loading": "убытие с погрузки",
    "arrived_unloading": "прибытие на выгрузку",
    "departed_unloading": "убытие с выгрузки",
    "delivered": "доставка подтверждена",
}

DOC_TITLES = {"waybill": "Накладная", "trip_sheet": "Путевой лист"}

# Латиница, похожая на кириллицу в госномерах (ГОСТ Р 50577 допускает только эти 12 букв).
_LAT2CYR = str.maketrans("ABEKMHOPCTYX", "АВЕКМНОРСТУХ")


@dataclass
class Finding:
    rule: str
    severity: str  # ok | info | warning | block
    message: str
    document: str | None = None
    field: str | None = None
    guilty: str | None = None  # customer | carrier
    amount: int | None = None

    def dict(self) -> dict:
        return asdict(self)


def norm_person(name: str | None) -> str:
    """«Иванов Ю.И.», «ИВАНОВ Ю. И.», «Иванов Юрий Иванович» → «ИВАНОВ Ю И»."""
    if not name:
        return ""
    s = name.upper().replace("Ё", "Е")
    parts = [p for p in re.split(r"[\s.]+", s) if p]
    if not parts:
        return ""
    surname, rest = parts[0], parts[1:]
    initials = []
    for p in rest:
        # «ЮИ» без точек и пробелов — два инициала
        if len(p) <= 2 and p.isalpha():
            initials.extend(list(p))
        else:
            initials.append(p[0])
    return " ".join([surname] + initials)


def norm_plate(plate: str | None) -> str:
    """«X133MT77RUS» (латиница) и «Х 133 МТ 77» → «Х133МТ77»."""
    if not plate:
        return ""
    s = re.sub(r"[\s\-]", "", plate.upper())
    s = re.sub(r"(RUS|РУС)$", "", s)
    return s.translate(_LAT2CYR)


def norm_license(lic: str | None) -> str:
    """«57МК № 113540» → «57МК113540»."""
    if not lic:
        return ""
    return re.sub(r"[^0-9A-ZА-Я]", "", lic.upper().replace("Ё", "Е")).translate(_LAT2CYR)


def _t(ev: dict | None) -> datetime | None:
    return ev["occurred_at"] if ev else None


def downtime(
    events: dict[str, dict], norm_min: int, rate_hour: int, arrive: str, leave: str, stage: str
) -> Finding:
    a, d = _t(events.get(arrive)), _t(events.get(leave))
    if not a or not d:
        return Finding("downtime_" + stage, "info", f"{stage.capitalize()}: время ещё не известно")
    actual = int((d - a).total_seconds() // 60)
    excess = max(0, actual - norm_min)
    if excess == 0:
        return Finding("downtime_" + stage, "ok", f"{stage.capitalize()}: {actual} мин при норме {norm_min} — в норме", amount=0)
    amount = excess * rate_hour // 60
    return Finding(
        "downtime_" + stage,
        "info",
        f"{stage.capitalize()}: {actual} мин при норме {norm_min}, сверх нормы {excess} мин × "
        f"{rub(rate_hour)}/ч = {rub(amount)}. Простой на стороне заказчика — компенсация перевозчику.",
        guilty="customer",
        amount=amount,
    )


def run(order: dict, waybill: dict | None, trip_sheet: dict | None, events: dict[str, dict]) -> list[Finding]:
    out: list[Finding] = []

    # 1. Наличие документов
    if not waybill:
        out.append(Finding("docs_present", "block", "Нет подписанной накладной (ТТН)", "waybill"))
    if not trip_sheet:
        out.append(Finding("docs_present", "block", "Нет путевого листа", "trip_sheet"))

    # 2. Обязательные события трека
    missing = [title for k, title in REQUIRED_EVENTS.items() if k not in events]
    if missing:
        out.append(Finding("events_complete", "block", "Не хватает событий: " + ", ".join(missing)))
    else:
        out.append(Finding("events_complete", "ok", "Все события рейса получены"))

    if waybill and trip_sheet:
        wn, tn = waybill.get("number"), trip_sheet.get("number")

        # 3. Водитель: надёжный ключ — номер удостоверения; если его нет, сравниваем ФИО.
        wl, tl = norm_license(waybill.get("driver_license")), norm_license(trip_sheet.get("driver_license"))
        wname, tname = waybill.get("driver_name", ""), trip_sheet.get("driver_name", "")
        # Совпало удостоверение — разница в написании ФИО не критична.
        same = (wl == tl) if (wl and tl) else (norm_person(wname) == norm_person(tname))
        if not same:
            t_who = (tname or "не указан") + (f" (уд. {trip_sheet['driver_license']})" if tl else "")
            w_who = (wname or "не указан") + (f" (уд. {waybill['driver_license']})" if wl else "")
            out.append(Finding(
                "driver_match", "block",
                f"Водитель не совпадает: в путевом листе №{tn} — {t_who}, в накладной №{wn} — {w_who}. "
                "Выплата заблокирована до исправленного путевого листа.",
                "trip_sheet", "driver_name", guilty="carrier"))
        else:
            out.append(Finding("driver_match", "ok", f"Водитель совпадает: {wname}"))

        # 4. Транспорт
        wp, tp = norm_plate(waybill.get("vehicle_plate")), norm_plate(trip_sheet.get("vehicle_plate"))
        if wp and tp and wp != tp:
            out.append(Finding(
                "vehicle_match", "block",
                f"Машина не совпадает: в накладной {waybill.get('vehicle_plate')}, "
                f"в путевом листе {trip_sheet.get('vehicle_plate')}",
                "trip_sheet", "vehicle_plate", guilty="carrier"))
        elif wp and tp:
            out.append(Finding("vehicle_match", "ok", f"Машина совпадает: {waybill.get('vehicle_plate')}"))
        if trip_sheet.get("vehicle_type") == "car":
            out.append(Finding(
                "vehicle_type", "block",
                "Путевой лист оформлен на легковой автомобиль — для грузового рейса нужен путевой лист грузового ТС",
                "trip_sheet", "vehicle_type", guilty="carrier"))

        # 5. Ссылка из накладной на путевой лист
        ref = waybill.get("trip_sheet_number")
        if ref and str(ref) != str(tn):
            out.append(Finding(
                "trip_sheet_ref", "block",
                f"Накладная №{wn} ссылается на путевой лист №{ref}, а подан путевой лист №{tn}",
                "waybill", "trip_sheet_number", guilty="carrier"))

        # 6. Предрейсовый медосмотр
        if trip_sheet.get("medical_check") is False:
            out.append(Finding(
                "medical_check", "block",
                f"В путевом листе №{tn} нет отметки о предрейсовом медосмотре водителя",
                "trip_sheet", "medical_check", guilty="carrier"))

    # 7. Простой на погрузке и выгрузке
    out.append(downtime(events, order["loading_norm_min"], order["downtime_rate_hour"],
                        "arrived_loading", "departed_loading", "погрузка"))
    out.append(downtime(events, order["unloading_norm_min"], order["downtime_rate_hour"],
                        "arrived_unloading", "departed_unloading", "выгрузка"))
    return out


def downtime_total(findings: list[Finding]) -> int:
    return sum(f.amount or 0 for f in findings if f.rule.startswith("downtime_") and f.guilty == "customer")


def blocking(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity == "block"]
