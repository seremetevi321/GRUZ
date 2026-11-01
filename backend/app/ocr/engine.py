"""Этапы 2–3. Поиск и распознавание текста: Tesseract 5 (нейросеть LSTM), русский и английский.

Возвращаем строки текста с координатами и уверенностью — по ним шаблон находит поля."""
from dataclasses import dataclass, field

import numpy as np
import pytesseract

LANG = "rus+eng"
TIMEOUT_S = 40


@dataclass
class Line:
    text: str
    conf: float              # 0..1
    box: tuple[int, int, int, int]  # x0, y0, x1, y1 в пикселях подготовленного изображения
    variant: str
    words: list = field(default_factory=list)

    @property
    def cy(self) -> float:
        return (self.box[1] + self.box[3]) / 2

    @property
    def h(self) -> int:
        return self.box[3] - self.box[1]


def recognize(img: np.ndarray, variant: str, psm: int = 11) -> list[Line]:
    d = pytesseract.image_to_data(img, lang=LANG, config=f"--oem 1 --psm {psm}",
                                  output_type=pytesseract.Output.DICT, timeout=TIMEOUT_S)
    groups: dict[tuple, list[int]] = {}
    for i, t in enumerate(d["text"]):
        if d["level"][i] == 5 and t.strip() and float(d["conf"][i]) >= 0:
            groups.setdefault((d["block_num"][i], d["par_num"][i], d["line_num"][i]), []).append(i)
    lines = []
    for idx in groups.values():
        idx.sort(key=lambda i: d["left"][i])
        words = [(d["text"][i], float(d["conf"][i]) / 100,
                  (d["left"][i], d["top"][i], d["left"][i] + d["width"][i], d["top"][i] + d["height"][i])) for i in idx]
        x0 = min(w[2][0] for w in words); y0 = min(w[2][1] for w in words)
        x1 = max(w[2][2] for w in words); y1 = max(w[2][3] for w in words)
        lines.append(Line(" ".join(w[0] for w in words), sum(w[1] for w in words) / len(words),
                          (x0, y0, x1, y1), variant, words))
    return lines
