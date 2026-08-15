"""
Seeds enrollment + a second ("follow-up") lab reading for a handful of
patients, against NCT04280705.

Step 7 (the real invite -> consent -> enroll flow) hasn't been built yet, so
patient_trial is empty, and Step 3's lab seed gave every patient exactly one
reading per test -- there is currently zero baseline-vs-latest data anywhere
in the dataset. Step 6 (progress tracking) needs both to exist to be
demoable at all. This script creates a small, clearly-synthetic cohort:

- ~15 patients already have an EGFR reading -> enrolled, baseline_date set
  to that reading's test_date, then a follow-up EGFR reading inserted
  60-150 days later with a perturbed value (mix of improved/worsened).
- ~3 patients tracked on HBA1C the same way, for variety.
- ~1 patient tracked on WBC (no defined improve/worsen direction) with a
  follow-up, to produce a real "indeterminate" case.
- ~3 enrolled patients get NO follow-up reading inserted, to produce real
  "no_data" cases (enrolled, but no new report yet).
- 2 patients are marked "withdrawn" instead of "enrolled", to exercise the
  active-vs-enrolled distinction.

Purely additive. Reversible via:
    DELETE FROM lab_results WHERE lab_report_id LIKE 'FU%';
    DELETE FROM patient_trial WHERE nct_id = 'NCT04280705';

Run once: venv/Scripts/python.exe scripts/seed_enrollment.py
"""

import os
import random
import string
from datetime import datetime, timedelta

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

NCT_ID = "NCT04280705"

# lower_is_better / higher_is_better perturbation direction per test.
PERTURB = {
    "EGFR": {"direction": "higher_is_better", "scale": 8},
    "HBA1C": {"direction": "lower_is_better", "scale": 0.6},
    "WBC": {"direction": None, "scale": 1.2},
}


def follow_up_report_id() -> str:
    digits = "".join(random.choices(string.digits, k=5))
    letter = random.choice(string.ascii_uppercase)
    return f"FU{digits}{letter}"


def perturbed_value(base_value: float, test_code: str, improve: bool) -> float:
    spec = PERTURB[test_code]
    delta = abs(random.gauss(spec["scale"], spec["scale"] * 0.4))
    if spec["direction"] == "higher_is_better":
        signed = delta if improve else -delta
    elif spec["direction"] == "lower_is_better":
        signed = -delta if improve else delta
    else:
        signed = random.choice([-1, 1]) * delta
    return round(max(0.1, base_value + signed), 4)


def main():
    client = create_client(os.environ["DATABASE_URL"], os.environ["SUPABASE_ANON_KEY"])

    client.table("trials").upsert({"nct_id": NCT_ID}, on_conflict="nct_id").execute()

    cohort = []  # (patient_id, test_code, baseline_row, add_follow_up, status)

    egfr_rows = client.table("lab_results").select("*").eq("test_code", "EGFR").limit(15).execute().data
    for row in egfr_rows:
        cohort.append((row["patient_id"], "EGFR", row, True, "enrolled"))

    # HBA1C/EGFR/ALT/AST were all seeded for the same 1000 patients in the
    # same per-patient loop (Step 3), so the first N rows of each test_code
    # tend to belong to the same patients -- a small .limit() here would
    # keep re-finding patients already claimed by the EGFR pick above and
    # silently add zero. Scan deep enough to guarantee finding unused ones.
    used_ids = {c[0] for c in cohort}
    hba1c_rows = client.table("lab_results").select("*").eq("test_code", "HBA1C").limit(150).execute().data
    added = 0
    for row in hba1c_rows:
        if row["patient_id"] in used_ids:
            continue
        cohort.append((row["patient_id"], "HBA1C", row, True, "enrolled"))
        used_ids.add(row["patient_id"])
        added += 1
        if added >= 3:
            break
    assert added == 3, f"only found {added}/3 unused HBA1C patients -- widen the scan"

    wbc_rows = client.table("lab_results").select("*").eq("test_code", "WBC").limit(150).execute().data
    added = 0
    for row in wbc_rows:
        if row["patient_id"] in used_ids:
            continue
        cohort.append((row["patient_id"], "WBC", row, True, "enrolled"))
        used_ids.add(row["patient_id"])
        added += 1
        break
    assert added == 1, "found 0 unused WBC patients -- widen the scan"

    # No-follow-up (no_data) cases: reuse 3 more EGFR patients, skip the new reading.
    added = 0
    for row in egfr_rows[15:] or client.table("lab_results").select("*").eq("test_code", "EGFR").range(15, 30).execute().data:
        if row["patient_id"] in used_ids:
            continue
        cohort.append((row["patient_id"], "EGFR", row, False, "enrolled"))
        used_ids.add(row["patient_id"])
        added += 1
        if added >= 3:
            break

    # Withdrawn patients: reuse 2 more EGFR patients with a follow-up already recorded.
    more_egfr = client.table("lab_results").select("*").eq("test_code", "EGFR").range(30, 45).execute().data
    added = 0
    for row in more_egfr:
        if row["patient_id"] in used_ids:
            continue
        cohort.append((row["patient_id"], "EGFR", row, True, "withdrawn"))
        used_ids.add(row["patient_id"])
        added += 1
        if added >= 2:
            break

    print(f"{len(cohort)} patients in seed cohort")

    patient_trial_rows = []
    lab_rows = []

    for i, (patient_id, test_code, baseline_row, add_follow_up, status) in enumerate(cohort):
        baseline_date = datetime.fromisoformat(baseline_row["test_date"])
        patient_trial_rows.append(
            {
                "patient_id": patient_id,
                "nct_id": NCT_ID,
                "status": status,
                "baseline_date": baseline_date.isoformat(),
                "enrolled_at": baseline_date.isoformat(),
                **({"withdrawn_at": (baseline_date + timedelta(days=200)).isoformat()} if status == "withdrawn" else {}),
            }
        )

        if add_follow_up:
            follow_up_date = baseline_date + timedelta(days=random.randint(60, 150))
            improve = i % 3 != 0  # roughly 2/3 improved, 1/3 worsened, for visible variety
            new_value = perturbed_value(baseline_row["value"], test_code, improve)
            lab_rows.append(
                {
                    "patient_id": patient_id,
                    "test_name": baseline_row["test_name"],
                    "test_code": test_code,
                    "value": new_value,
                    "unit": baseline_row["unit"],
                    "reference_min": baseline_row["reference_min"],
                    "reference_max": baseline_row["reference_max"],
                    "test_date": follow_up_date.isoformat(),
                    "abnormal_flag": not (baseline_row["reference_min"] <= new_value <= baseline_row["reference_max"]),
                    "lab_report_id": follow_up_report_id(),
                }
            )

    client.table("patient_trial").upsert(patient_trial_rows, on_conflict="patient_id,nct_id").execute()
    print(f"enrolled {len(patient_trial_rows)} patients")

    if lab_rows:
        client.table("lab_results").insert(lab_rows).execute()
        print(f"inserted {len(lab_rows)} follow-up lab readings")

    print("done")


if __name__ == "__main__":
    main()
