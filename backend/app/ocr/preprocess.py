"""Этап 1. Предобработка: размер, серый, шумоподавление, выравнивание наклона, бинаризация.

Делаем несколько вариантов картинки: какой-то лучше подходит печатному тексту,
какой-то — синим чернилам на сером фото. Распознаём все и берём лучшее по каждому полю.
"""
import cv2
import numpy as np
from PIL import Image

TARGET = 2000  # длинная сторона после масштабирования, px


def to_cv(im: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR)


def normalize_size(bgr: np.ndarray) -> np.ndarray:
    h, w = bgr.shape[:2]
    k = TARGET / max(h, w)
    if 0.9 < k < 1.1:
        return bgr
    return cv2.resize(bgr, (round(w * k), round(h * k)), interpolation=cv2.INTER_CUBIC if k > 1 else cv2.INTER_AREA)


def skew_angle(gray: np.ndarray) -> float:
    """Угол наклона по линиям бланка и строкам текста (медиана почти горизонтальных отрезков)."""
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 1800, threshold=200,
                            minLineLength=gray.shape[1] // 6, maxLineGap=10)
    if lines is None:
        return 0.0
    angles = []
    for x1, y1, x2, y2 in lines[:, 0]:
        a = np.degrees(np.arctan2(y2 - y1, x2 - x1))
        if abs(a) < 15:
            angles.append(a)
    return float(np.median(angles)) if len(angles) >= 3 else 0.0


def rotate(img: np.ndarray, angle: float) -> np.ndarray:
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    border = (255, 255, 255) if img.ndim == 3 else 255
    return cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_CUBIC, borderValue=border)


def prepare(im: Image.Image) -> tuple[np.ndarray, dict[str, np.ndarray], float]:
    """Возвращает выровненное цветное изображение (для превью) и варианты для распознавания."""
    bgr = normalize_size(to_cv(im))
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    angle = skew_angle(gray)
    if 0.2 < abs(angle) < 15:
        bgr = rotate(bgr, angle)
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    else:
        angle = 0.0

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

    # A. Печатный текст: шумоподавление + адаптивная бинаризация (устойчива к теням).
    den = cv2.GaussianBlur(gray, (3, 3), 0)
    binar = cv2.adaptiveThreshold(den, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 41, 15)

    # B. Синие и фиолетовые чернила: в красном канале они темнее всего. Плюс локальный контраст.
    red = clahe.apply(bgr[:, :, 2])

    # C. Серое с локальным контрастом — бинаризацию отдаём самому Tesseract (Оцу).
    soft = clahe.apply(gray)

    return bgr, {"binary": binar, "ink": red, "gray": soft}, angle
