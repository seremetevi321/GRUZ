"""Конвейер анализа документа."""
import base64
import os
import time
from concurrent.futures import ThreadPoolExecutor

import cv2

from . import template
from .engine import recognize
from .loader import LoadError, load_pages
from .preprocess import prepare

# Tesseract сам распараллеливается и на нескольких одновременных вызовах тормозит;
# выгоднее один поток на вариант и варианты параллельно.
os.environ.setdefault("OMP_THREAD_LIMIT", "1")

OK_THRESHOLD = 0.8  # ниже — поле подсвечивается «проверьте»

FIELDS = {
    "waybill": ["number", "date", "trip_sheet_number", "driver_name", "driver_license", "vehicle_model", "vehicle_plate"],
    "trip_sheet": ["number", "date", "driver_name", "driver_license", "vehicle_model", "vehicle_plate",
                   "vehicle_type", "medical_check"],
}
TITLES = {"waybill": "Накладная (ТТН)", "trip_sheet": "Путевой лист"}

_pool = ThreadPoolExecutor(max_workers=3)


class AnalysisError(Exception):
    pass


def _preview(bgr) -> tuple[str, int, int]:
    h, w = bgr.shape[:2]
    k = min(1.0, 1400 / max(h, w))
    small = cv2.resize(bgr, (round(w * k), round(h * k)), interpolation=cv2.INTER_AREA) if k < 1 else bgr
    ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode(), small.shape[1], small.shape[0]


def analyze(data: bytes, expected_type: str | None = None) -> dict:
    t0 = time.monotonic()
    try:
        mime, pages = load_pages(data)
    except LoadError as e:
        raise AnalysisError(str(e)) from e
    warnings = []
    if len(pages) > 1:
        warnings.append("Распознана первая страница документа")

    bgr, variants, angle = prepare(pages[0])
    if abs(angle) >= 0.2:
        warnings.append(f"Изображение выровнено: наклон {angle:.1f}°")
    per_variant = dict(zip(variants, _pool.map(lambda kv: recognize(kv[1], kv[0]), variants.items())))
    all_lines = [l for ls in per_variant.values() for l in ls]
    if sum(len(l.text) for l in all_lines) < 40:
        raise AnalysisError("На изображении почти нет текста. Сфотографируйте документ ближе и при хорошем освещении.")

    detected, type_conf = template.classify(all_lines)
    doc_type = expected_type or detected
    if doc_type not in template.DOC_TYPES:
        raise AnalysisError("Не удалось определить тип документа. Выберите его вручную.")
    if expected_type and detected and detected != expected_type:
        warnings.append(f"Похоже, это {TITLES[detected].lower()}, а не {TITLES[expected_type].lower()}")

    H, W = bgr.shape[:2]
    cands = template.extract(doc_type, per_variant)
    out = {}
    for key in FIELDS[doc_type]:
        best, conf = template.vote(cands.get(key, []))
        if best is None:
            out[key] = {"value": None, "confidence": 0.0, "status": "missing", "box": None, "source": None}
            continue
        x0, y0, x1, y1 = best.box or best.line.box
        out[key] = {
            "value": best.value,
            "confidence": conf,
            "status": "ok" if conf >= OK_THRESHOLD else "check",
            "box": [round(x0 / W, 4), round(y0 / H, 4), round(x1 / W, 4), round(y1 / H, 4)],
            "source": best.line.text[:120],
        }

    preview, pw, ph = _preview(bgr)
    return {
        "doc_type": doc_type,
        "detected_type": detected,
        "type_confidence": type_conf,
        "fields": out,
        "warnings": warnings,
        "preview": preview,
        "preview_size": [pw, ph],
        "source_mime": mime,
        "elapsed_ms": round((time.monotonic() - t0) * 1000),
    }
