"""Схемы входных данных. Лишние поля запрещены, длины и суммы ограничены."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Kop = Field(gt=0, le=10**12)  # до 10 млрд ₽ в копейках


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoginIn(Strict):
    login: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class OrderIn(Strict):
    carrier_id: int = Field(gt=0)
    route_from: str = Field(min_length=1, max_length=200)
    route_to: str = Field(min_length=1, max_length=200)
    cargo: str = Field(min_length=1, max_length=200)
    price: int = Kop


class PriceIn(Strict):
    amount: int = Kop


class DisputeIn(Strict):
    comment: str = Field(min_length=1, max_length=1000)


class ResolveIn(Strict):
    amount: int = Kop
    comment: str = Field(min_length=1, max_length=1000)


class WaybillData(Strict):
    number: str = Field(min_length=1, max_length=50)
    date: str | None = Field(default=None, max_length=20)
    trip_sheet_number: str | None = Field(default=None, max_length=50)
    driver_name: str = Field(min_length=1, max_length=120)
    driver_license: str | None = Field(default=None, max_length=40)
    vehicle_plate: str | None = Field(default=None, max_length=20)
    vehicle_model: str | None = Field(default=None, max_length=60)


class TripSheetData(Strict):
    number: str = Field(min_length=1, max_length=50)
    date: str | None = Field(default=None, max_length=20)
    driver_name: str = Field(min_length=1, max_length=120)
    driver_license: str | None = Field(default=None, max_length=40)
    vehicle_plate: str | None = Field(default=None, max_length=20)
    vehicle_model: str | None = Field(default=None, max_length=60)
    vehicle_type: Literal["truck", "car"] = "truck"
    medical_check: bool = True


class EventIn(Strict):
    event_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_\-:.]+$")
    kind: Literal["waybill_signed", "trip_sheet_issued", "departed", "arrived_loading", "departed_loading",
                  "arrived_unloading", "departed_unloading", "delivered"]
    occurred_at: datetime
    waybill: WaybillData | None = None
    trip_sheet: TripSheetData | None = None


class DocumentIn(Strict):
    """Документ, подтверждённый перевозчиком после распознавания (или введённый вручную)."""
    doc_type: Literal["waybill", "trip_sheet"]
    scan_id: int | None = Field(default=None, gt=0)
    waybill: WaybillData | None = None
    trip_sheet: TripSheetData | None = None
