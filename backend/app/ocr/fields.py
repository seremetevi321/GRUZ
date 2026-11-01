"""Этап 4. Постпроцессинг: проверка и нормализация значений по смыслу поля.

Вместо словаря или языковой модели — строгие форматы: госномер из 12 разрешённых букв,
удостоверение «57МК 113540», ФИО «Фамилия И.О.». Типичные путаницы OCR (0/О, 3/З, I/1)
исправляются только там, где формат однозначно требует цифру или букву.
Если значение не укладывается в формат, его лучше не выдавать, чем выдать неверное.
"""
import re

LAT2CYR = str.maketrans("ABEKMHOPCTYXaekmhopctyx", "АВЕКМНОРСТУХаекмнорстух")
PLATE_LETTERS = set("АВЕКМНОРСТУХ")
# В позиции цифры: что OCR обычно путает с цифрами.
TO_DIGIT = {"О": "0", "O": "0", "Q": "0", "D": "0", "З": "3", "I": "1", "L": "1", "|": "1", "!": "1",
            "S": "5", "Z": "2", "B": "8", "В": "8", "Т": "7", "T": "7"}
# В позиции буквы госномера: цифры, похожие на разрешённые буквы.
TO_LETTER = {"0": "О", "8": "В"}

MONTHS = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6, "июля": 7,
          "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12}

BRANDS = {
    "камаз": "КамАЗ", "маз": "МАЗ", "газ": "ГАЗ", "газель": "ГАЗель", "зил": "ЗИЛ", "урал": "Урал",
    "volvo": "Volvo", "scania": "Scania", "man": "MAN", "mercedes": "Mercedes-Benz", "daf": "DAF",
    "iveco": "Iveco", "renault": "Renault", "shacman": "Shacman", "sitrak": "Sitrak", "howo": "Howo",
    "faw": "FAW", "hyundai": "Hyundai", "isuzu": "Isuzu", "hino": "Hino", "ford": "Ford", "skoda": "Skoda",
    "лада": "Лада", "lada": "LADA", "kia": "Kia", "toyota": "Toyota", "dongfeng": "Dongfeng", "jac": "JAC",
}
_BRAND_RE = re.compile(r"(?<![\wа-яё])(" + "|".join(sorted(BRANDS, key=len, reverse=True)) + r")(?![\wа-яё])", re.I)


def plate(text: str, near_label: bool = True) -> str | None:
    """Российский госномер: буква, 3 цифры, 2 буквы, регион из 2–3 цифр.
    Без подписи поля рядом цифры в буквы не превращаем: иначе «номер» найдётся в банковском счёте."""
    s = re.sub(r"[^0-9A-ZА-ЯЁ|!]", "", text.upper())
    s = re.sub(r"(RUS|РУС)$", "", s).translate(LAT2CYR)
    for i in range(len(s) - 7):
        for n in (9, 8):
            chunk = s[i:i + n]
            if len(chunk) < n:
                continue
            out = []
            for pos, ch in enumerate(chunk):
                letter_slot = pos in (0, 4, 5)
                if letter_slot:
                    ch = TO_LETTER.get(ch, ch) if near_label else ch
                    if ch not in PLATE_LETTERS:
                        break
                else:
                    ch = TO_DIGIT.get(ch, ch)
                    if not ch.isdigit():
                        break
                out.append(ch)
            else:
                return "".join(out)
    return None


def license_no(text: str, near_label: bool) -> str | None:
    """Водительское удостоверение: «57МК 113540» (старый образец) или «99 12 345678» (новый)."""
    s = text.upper().replace("№", " ").replace("NE ", " ").replace("_", " ")
    m = re.search(r"(?<![\dА-ЯA-Z])([0-9ОOЗIS]{2})\s*([A-ZА-Я0]{2})\s*N?\s*([0-9ОOЗIS]{6})(?!\d)", s)
    if m:
        a = "".join(TO_DIGIT.get(c, c) for c in m.group(1))
        c = "".join(TO_DIGIT.get(x, x) for x in m.group(3))
        b = m.group(2).translate(LAT2CYR).replace("0", "О")
        if a.isdigit() and c.isdigit() and all("А" <= x <= "Я" for x in b):
            return f"{a}{b} {c}"
    if near_label:  # цифровой формат легко спутать с телефоном или счётом — только рядом с подписью поля
        m = re.search(r"(?<!\d)(\d{2})\s?(\d{2})\s?(\d{6})(?!\d)", s)
        if m:
            return f"{m.group(1)} {m.group(2)} {m.group(3)}"
    return None


def person(text: str) -> str | None:
    """«Иванов Ю.И.», «Иванов ИИ», «Иванов Юрий Иванович»."""
    s = re.sub(r"[\[\]|_«»\"“”]", " ", text)
    m = re.search(r"([А-ЯЁ][а-яё]{2,30}(?:-[А-ЯЁ][а-яё]+)?)\s+([А-ЯЁ][а-яё]+)\s+([А-ЯЁ][а-яё]+(?:вич|вна|ич|кызы|оглы))", s)
    if m:
        return " ".join(m.groups())
    m = re.search(r"([А-ЯЁ][а-яё]{2,30}(?:-[А-ЯЁ][а-яё]+)?)\s+([А-ЯЁ])\.?\s*([А-ЯЁ])\.?(?![а-яё])", s)
    if m:
        return f"{m.group(1)} {m.group(2)}.{m.group(3)}."
    return None


def number(text: str) -> str | None:
    s = text.replace("№", " ").replace("N", " ")
    m = re.search(r"(?<![\d.,/-])(\d{1,10})(?![\d.,/-])", s)
    if m and not re.fullmatch(r"(19|20)\d\d", m.group(1)):
        return m.group(1)
    return None


def date(text: str) -> str | None:
    s = text.lower().replace("ё", "е")
    s = re.sub(r"(приказ\w*|от)\s*\d{1,2}[.\-/]\d{1,2}[.\-/]\d{4}", " ", s)  # ссылки на нормативные акты — не дата документа
    m = re.search(r"(?<!\d)(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})(?!\d)", s)
    if m:
        d, mo, y = map(int, m.groups())
        if 1 <= d <= 31 and 1 <= mo <= 12 and 1990 <= y <= 2100:
            return f"{d:02d}.{mo:02d}.{y}"
    m = re.search(r"(\d{1,2})\D{0,6}?(" + "|".join(MONTHS) + r")\D{0,8}?(\d{4})", s)
    if m:
        d, y = int(m.group(1)), int(m.group(3))
        if 1 <= d <= 31 and 1990 <= y <= 2100:
            return f"{d:02d}.{MONTHS[m.group(2)]:02d}.{y}"
    return None


CYR2LAT = str.maketrans("АВЕКМНОРСТУХаекмнорстух", "ABEKMHOPCTYXaekmhopctyx")


def brand(text: str) -> tuple[str, str] | None:
    """Марка и то, что идёт за ней в той же строке (модель)."""
    m = _BRAND_RE.search(text)
    if not m:
        return None
    name = BRANDS[m.group(1).lower()]
    rest = text[m.end():].strip(" ,.;:—-_|")
    rest = re.match(r"[\w.\- ]{0,30}", rest).group(0).strip() if rest else ""
    if name.isascii():  # у иностранной марки модель латиницей: «FH 460», а не «ЕН 460»
        rest = rest.translate(CYR2LAT)
    return name, rest


MODEL_CODE = re.compile(r"^\s*(\d{2,4}-\d{2,4}[A-ZА-Я0-9]*)\s*$")
