"""Деньги — целые копейки. Форматирование для человекочитаемой ленты."""


def rub(kop: int) -> str:
    sign = "−" if kop < 0 else ""
    kop = abs(kop)
    r, k = divmod(kop, 100)
    s = f"{r:,}".replace(",", " ")
    return f"{sign}{s},{k:02d} ₽" if k else f"{sign}{s} ₽"


def percent(amount: int, pct: int) -> int:
    """pct% от суммы с округлением вниз до копейки."""
    return amount * pct // 100
