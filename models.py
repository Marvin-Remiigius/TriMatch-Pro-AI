from datetime import date
from typing import Optional

from pydantic import BaseModel


class Diagnosis(BaseModel):
    icd10: str
    label: str


class LabResult(BaseModel):
    name: str
    value: float
    unit: str
    date: date


class Vitals(BaseModel):
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    systolic_bp: Optional[int] = None
    diastolic_bp: Optional[int] = None
    heart_rate: Optional[int] = None
    spo2: Optional[int] = None
    temperature_c: Optional[float] = None


class Patient(BaseModel):
    id: str
    age: int
    sex: str
    diagnoses: list[Diagnosis] = []
    labs: list[LabResult] = []
    medications: list[str] = []
    vitals: Vitals = Vitals()
