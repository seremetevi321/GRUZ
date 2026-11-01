"""Извлечение полей по шаблону бланка.

Бланки ТТН и путевого листа заполняются по одному образцу, поэтому каждое поле ищется
рядом со своей подписью («Водитель», «Государственный номерной знак»…): сначала в той же
строке, затем справа в том же ряду, затем чуть ниже или выше. Найденное значение
проверяется форматом (fields.py). Каждый вариант предобработки голосует, побеждает
значение с лучшей суммарной оценкой.
"""
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from . import fields as F
from .engine import Line

DOC_TYPES = ("waybill", "trip_sheet")


@dataclass
class Cand:
    value: object
    score: float
    line: Line
    box: tuple | None = None


def _canon(s: str) -> str:
    return re.sub(r"[^0-9A-ZА-ЯЁ]", "", str(s).upper()).translate(F.LAT2CYR)


def value_words(line: Line, value) -> tuple[float, tuple]:
    """Уверенность и рамка по словам, из которых взято значение, а не по всей строке."""
    key = _canon(value)
    words = line.words

    def matches(t: str, loose: bool) -> bool:
        if not t:
            return False
        if len(t) >= 2 and (t in key or key in t):
            return True
        return SequenceMatcher(None, t, key).ratio() >= (0.35 if loose else 0.6) or (len(t) == 1 and t in key and loose)

    idx = [i for i, w in enumerate(words) if matches(_canon(w[0]), False)]
    if not idx:
        return line.conf, line.box
    lo, hi = min(idx), max(idx)
    # соседние куски того же значения («К» перед «456 ОР 199», «77ОА» перед «482913»)
    while lo > 0 and matches(_canon(words[lo - 1][0]), True):
        lo -= 1
    while hi < len(words) - 1 and matches(_canon(words[hi + 1][0]), True):
        hi += 1
    picked = words[lo:hi + 1]
    conf = sum(w[1] for w in picked) / len(picked)
    box = (min(w[2][0] for w in picked), min(w[2][1] for w in picked),
           max(w[2][2] for w in picked), max(w[2][3] for w in picked))
    return conf, box


def make(value, raw: str, line: Line, weight: float) -> "Cand":
    conf, box = value_words(line, value)
    return Cand(value, conf * weight, line, box)


def _low(s: str) -> str:
    return s.lower().replace("ё", "е")


def find_labels(lines: list[Line], pattern: str) -> list[Line]:
    rx = re.compile(pattern)
    return [l for l in lines if rx.search(_low(l.text))]


def same_row(a: Line, b: Line) -> bool:
    tol = 0.65 * max(a.h, b.h, 12)
    return abs(a.cy - b.cy) <= tol


def near(label: Line, lines: list[Line], below: float = 2.5, above: float = 1.2) -> list[tuple[Line, float]]:
    """Строки рядом с подписью и вес по геометрии: та же строка → справа в ряду → ниже/выше."""
    out = [(label, 1.0)]
    h = max(label.h, 12)
    for l in lines:
        if l is label:
            continue
        if same_row(label, l) and l.box[0] >= label.box[0]:
            gap = max(0, l.box[0] - label.box[2])
            out.append((l, 1.0 if gap < 25 * h else 0.75))
        else:
            dy = l.cy - label.cy
            horiz = l.box[2] >= label.box[0] - 4 * h
            if horiz and (0 < dy <= below * h or -above * h <= dy < 0):
                out.append((l, 0.8 - 0.08 * abs(dy) / h))
    return out


def text_after_label(line: Line, pattern: str) -> str:
    m = re.search(pattern, _low(line.text))
    return line.text[m.end():] if m else line.text


# ---------- правила полей ----------

def _by_label(lines, label_rx, parse, base_fallback=None):
    """Значение возле подписи; если подписи нет — по формату по всему листу с пониженной оценкой."""
    cands = []
    for lab in find_labels(lines, label_rx):
        for l, w in near(lab, lines):
            txt = text_after_label(l, label_rx) if l is lab else l.text
            v = parse(txt, True)
            if v:
                cands.append(make(v, txt, l, w))
    if not cands and base_fallback is not None:
        for l in lines:
            v = parse(l.text, False)
            if v:
                cands.append(make(v, l.text, l, base_fallback))
    return cands


def driver(lines):
    return _by_label(lines, r"водитель(?!ю)", lambda t, _: F.person(t))


def driver_license(lines):
    return _by_label(lines, r"удостовер|верени", F.license_no, base_fallback=0.5)


def vehicle_plate(lines):
    return _by_label(lines, r"номерн\w*\s*(зна|ни|нм)|номерной|госуд\w*", lambda t, near: F.plate(t, near), base_fallback=0.6)


def vehicle_model(lines):
    cands = []
    for l in lines:
        b = F.brand(l.text)
        if not b:
            continue
        name, rest = b
        model = rest
        if not model:  # «КамАЗ» и «53-215» часто стоят двумя строками одна под другой
            h = max(l.h, 12)
            for m in lines:
                if m is not l and 0 < m.cy - l.cy <= 2.5 * h and abs((m.box[0] + m.box[2]) - (l.box[0] + l.box[2])) < 8 * h:
                    mm = F.MODEL_CODE.match(m.text)
                    if mm:
                        model = mm.group(1)
                        break
        labelled = any(same_row(l, lab) or 0 < l.cy - lab.cy < 3 * lab.h or 0 < lab.cy - l.cy < 3 * lab.h
                       for lab in find_labels(lines, r"марка|автомоб"))
        cands.append(Cand(f"{name} {model}".strip(), l.conf * (1.0 if labelled else 0.75), l))
    return cands


def waybill_number(lines):
    rx = r"(ттн|tth|thn|ттм|тнн)"
    cands = _by_label(lines, rx, lambda t, _: F.number(t))
    if not cands:  # подпись не распозналась: номер ТТН стоит отдельным числом в правом верхнем углу
        W = max(l.box[2] for l in lines)
        H = max(l.box[3] for l in lines)
        for l in lines:
            if l.box[0] > 0.75 * W and l.cy < 0.12 * H and re.fullmatch(r"\s*\d{1,10}\s*", l.text):
                cands.append(Cand(l.text.strip(), l.conf * 0.6, l))
    return cands


def trip_sheet_ref(lines, number_line: Line | None):
    """«К путевому листу №» — узкая вертикальная подпись в правой колонке ТТН, под номером ТТН."""
    cands = []
    rx = r"путев\w*\s*(ому|лист)\w*"
    for lab in find_labels(lines, rx):
        for l, w in near(lab, lines, below=4):
            txt = text_after_label(l, rx) if l is lab else l.text
            v = F.number(txt) if re.fullmatch(r"\s*([№N]|Ne)?\s*\d{1,10}\s*", txt) else None
            if v:
                cands.append(make(v, txt, l, w))
    if not cands and number_line is not None:
        W = max(l.box[2] for l in lines)
        H = max(l.box[3] for l in lines)
        for l in lines:
            if (l is not number_line and l.box[0] > 0.8 * W and number_line.cy < l.cy < 0.35 * H
                    and re.fullmatch(r"\s*\d{1,10}\s*", l.text)):
                cands.append(Cand(l.text.strip(), l.conf * 0.55, l))
    return cands


def trip_sheet_number(lines):
    cands = []
    for lab in find_labels(lines, r"путев\w*\s+лист"):
        row = sorted([l for l in lines if same_row(lab, l) and l.box[0] >= lab.box[0]], key=lambda l: l.box[0])
        for l in row:
            txt = text_after_label(l, r"путев\w*\s+лист\w*(\s+\w+\s+\w+)?") if l is lab else l.text
            v = F.number(txt)
            if v:
                cands.append(make(v, txt, l, 1.0))
                break
    return cands


def doc_date(lines, label_rx: str | None):
    cands = []
    if label_rx:
        for lab in find_labels(lines, label_rx):
            row = sorted([l for l in lines if same_row(lab, l) and l.box[0] >= lab.box[0]], key=lambda l: l.box[0])
            v = F.date(" ".join(l.text for l in row))
            if v:
                c = Cand(v, sum(l.conf for l in row) / len(row), lab)
                c.box = (min(l.box[0] for l in row), min(l.box[1] for l in row),
                         max(l.box[2] for l in row), max(l.box[3] for l in row))
                cands.append(c)
    if not cands:  # первая полная дата сверху листа
        dated = [(l, F.date(l.text)) for l in lines]
        dated = [(l, v) for l, v in dated if v]
        if dated:
            l, v = min(dated, key=lambda x: x[0].cy)
            cands.append(Cand(v, l.conf * 0.7, l))
    return cands


def vehicle_type(lines):
    for l in find_labels(lines, r"путев\w*\s+лист"):
        t = _low(l.text)
        if "легков" in t:
            return [Cand("car", l.conf, l)]
        if "грузов" in t:
            return [Cand("truck", l.conf, l)]
    return []


def medical_check(lines):
    """Есть ли в строке предрейсового медосмотра настоящая дата, а не шаблон «ДД.ММ.ГГГГ»."""
    cands = []
    for lab in find_labels(lines, r"меди[цн]\w*ск|медицин"):
        if "послерейс" in _low(lab.text):
            continue
        h = max(lab.h, 18)
        rows = [l for l in lines if 0.6 * h < l.cy - lab.cy <= 3 * h
                and not re.search(r"контрол|послерейс|меди[цн]", _low(l.text))]
        rows.sort(key=lambda l: -len(l.text))
        if not rows:
            continue
        txt = " ".join(l.text for l in rows)
        if F.date(txt):
            cands.append(Cand(True, 0.7, rows[0]))
        elif re.search(r"д\s*д|м\s*м|гг|дд|мм", _low(txt)) or rows:
            cands.append(Cand(False, 0.6, rows[0]))
    return cands


# ---------- тип документа ----------

KEYWORDS = {
    "trip_sheet": [r"путев\w*\s+лист", r"одометр", r"горюч", r"медицинск", r"механик", r"выезд\s+разреш", r"парковк"],
    "waybill": [r"пункт\s+по?груз", r"пункт\s+р\w*груз", r"сведения\s+о\s+груз", r"срок\s+доставк",
                r"масса\s+брутто", r"(ттн|tth)", r"заказчик", r"грузоотправ|грузополуч", r"пломб"],
}


def classify(lines: list[Line]) -> tuple[str | None, float]:
    text = _low(" \n".join(l.text for l in lines))
    hits = {t: sum(1 for k in ks if re.search(k, text)) for t, ks in KEYWORDS.items()}
    best = max(hits, key=hits.get)
    other = min(hits, key=hits.get)
    if hits[best] == 0:
        return None, 0.0
    return best, round(min(0.99, 0.5 + 0.1 * (hits[best] - hits[other])), 2)


# ---------- сборка ----------

def extract(doc_type: str, per_variant: dict[str, list[Line]]) -> dict[str, list[Cand]]:
    out: dict[str, list[Cand]] = {}
    for lines in per_variant.values():
        if not lines:
            continue
        found = {
            "driver_name": driver(lines),
            "driver_license": driver_license(lines),
            "vehicle_plate": vehicle_plate(lines),
            "vehicle_model": vehicle_model(lines),
        }
        if doc_type == "waybill":
            num = waybill_number(lines)
            found["number"] = num
            best_num = max(num, key=lambda c: c.score).line if num else None
            found["trip_sheet_number"] = trip_sheet_ref(lines, best_num)
            found["date"] = doc_date(lines, r"срок\s+достав")
        else:
            found["number"] = trip_sheet_number(lines)
            found["date"] = doc_date(lines, None)
            found["vehicle_type"] = vehicle_type(lines)
            found["medical_check"] = medical_check(lines)
        for k, v in found.items():
            out.setdefault(k, []).extend(v)
    return out


def vote(cands: list[Cand]) -> tuple[Cand | None, float]:
    """Одинаковое значение из разных вариантов усиливает уверенность."""
    if not cands:
        return None, 0.0
    groups: dict[object, list[Cand]] = {}
    for c in cands:
        groups.setdefault(c.value, []).append(c)
    variants = lambda cs: len({c.line.variant for c in cs})  # noqa: E731
    best_val = max(groups, key=lambda v: max(c.score for c in groups[v]) + 0.12 * (variants(groups[v]) - 1))
    g = groups[best_val]
    top = max(g, key=lambda c: c.score)
    conf = min(0.99, top.score + 0.08 * (variants(g) - 1))
    if len(groups) > 1:  # есть конкурирующие прочтения — снижаем уверенность
        conf -= 0.1
    return top, round(max(conf, 0.0), 2)
