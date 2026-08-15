"""
Adds HbA1c, eGFR, ALT, AST lab results for every patient in Supabase.

The existing `lab_results` table has exactly one row per patient, drawn from
5 basic tests (Cholesterol, Creatinine, Glucose, Hemoglobin, WBC). Most real
trial eligibility criteria reference labs outside that set (HbA1c for
diabetes trials, eGFR/ALT/AST for kidney/liver exclusion criteria), so those
criteria can never resolve against real patient data. This script adds one
row per patient for each of those 4 tests, matching the existing table's
conventions (lab_report_id format, reference ranges, abnormal_flag).

Purely additive -- does not touch the existing 1000 rows. Reversible via:
    DELETE FROM lab_results WHERE test_code IN ('HBA1C','EGFR','ALT','AST');

Run once: venv/Scripts/python.exe scripts/seed_extra_labs.py
"""

import os
import random
import string
from datetime import datetime, timedelta

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

TESTS = {
    "HBA1C": {"name": "HbA1c", "unit": "%", "ref_min": 4.0, "ref_max": 5.6},
    "EGFR": {"name": "eGFR", "unit": "mL/min/1.73m2", "ref_min": 90, "ref_max": 120},
    "ALT": {"name": "Alanine Aminotransferase (ALT)", "unit": "U/L", "ref_min": 7, "ref_max": 56},
    "AST": {"name": "Aspartate Aminotransferase (AST)", "unit": "U/L", "ref_min": 10, "ref_max": 40},
}

BATCH_SIZE = 500
DATE_START = datetime(2024, 9, 1)
DATE_END = datetime(2026, 6, 1)


def random_lab_report_id() -> str:
    digits = "".join(random.choices(string.digits, k=5))
    letter = random.choice(string.ascii_uppercase)
    return f"LR{digits}{letter}"


def random_test_date() -> str:
    span = (DATE_END - DATE_START).days
    dt = DATE_START + timedelta(days=random.randint(0, span), seconds=random.randint(0, 86399))
    return dt.isoformat()


def generate_value(test_code: str) -> float:
    spec = TESTS[test_code]
    if test_code == "EGFR":
        # ~85% within/near normal range, ~15% reduced kidney function (CKD tail)
        if random.random() < 0.85:
            value = random.gauss(95, 15)
        else:
            value = random.gauss(45, 20)
        return round(max(5.0, min(value, 140.0)), 4)

    mid = (spec["ref_min"] + spec["ref_max"]) / 2
    spread = (spec["ref_max"] - spec["ref_min"]) / 3
    value = random.gauss(mid, spread)
    return round(max(0.0, value), 4)


def build_row(patient_id: str, test_code: str) -> dict:
    spec = TESTS[test_code]
    value = generate_value(test_code)
    return {
        "patient_id": patient_id,
        "test_name": spec["name"],
        "test_code": test_code,
        "value": value,
        "unit": spec["unit"],
        "reference_min": spec["ref_min"],
        "reference_max": spec["ref_max"],
        "test_date": random_test_date(),
        "abnormal_flag": not (spec["ref_min"] <= value <= spec["ref_max"]),
        "lab_report_id": random_lab_report_id(),
    }


def main():
    client = create_client(os.environ["DATABASE_URL"], os.environ["SUPABASE_ANON_KEY"])

    patient_ids = []
    offset = 0
    while True:
        res = client.table("patients").select("patient_id").range(offset, offset + 999).execute()
        if not res.data:
            break
        patient_ids.extend(r["patient_id"] for r in res.data)
        offset += 1000

    print(f"{len(patient_ids)} patients found")

    rows = [
        build_row(pid, test_code)
        for pid in patient_ids
        for test_code in TESTS
    ]
    print(f"{len(rows)} rows to insert ({len(TESTS)} tests x {len(patient_ids)} patients)")

    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        client.table("lab_results").insert(batch).execute()
        print(f"  inserted {i + len(batch)}/{len(rows)}")

    print("done")


if __name__ == "__main__":
    main()
