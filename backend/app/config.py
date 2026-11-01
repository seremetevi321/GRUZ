"""Настройки из переменных окружения. Секретов в коде нет."""
import os


def _list(name: str, default: str) -> list[str]:
    return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]


DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://postgres@127.0.0.1:5432/escrow"
)
SESSION_TTL_HOURS = int(os.environ.get("SESSION_TTL_HOURS", "8"))
# Демо-режим включает симулятор внешних систем и кнопку сброса данных.
# В пилоте выключается: события приходят от ЭДО/телематики по mTLS.
DEMO_MODE = os.environ.get("DEMO_MODE", "1") == "1"
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "demo2026")
ALLOWED_ORIGINS = _list(
    "ALLOWED_ORIGINS",
    "tauri://localhost,http://tauri.localhost,https://tauri.localhost,http://localhost:5173,http://127.0.0.1:5173",
)
MAX_BODY_BYTES = 64 * 1024
SCAN_MAX_BYTES = 10 * 1024 * 1024
SCAN_CONCURRENCY = int(os.environ.get("SCAN_CONCURRENCY", "2"))  # распознавание тяжёлое: ограничиваем параллельные задачи
