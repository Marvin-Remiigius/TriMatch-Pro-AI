import json
from functools import lru_cache
from pathlib import Path

from models import Patient

DATA_FILE = Path(__file__).parent / "data" / "patients.json"


@lru_cache
def load_patients() -> list[Patient]:
    with open(DATA_FILE) as f:
        raw = json.load(f)
    return [Patient(**p) for p in raw]


def get_patient(patient_id: str) -> Patient | None:
    for patient in load_patients():
        if patient.id == patient_id:
            return patient
    return None
