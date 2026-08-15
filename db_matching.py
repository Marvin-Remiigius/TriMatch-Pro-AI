import json

from matching import _evaluate_condition, _normalize_op
from models import CriterionMatch

# The parser (llm.py) writes lowercase snake_case lab names, e.g. "lab.hba1c",
# "lab.cholesterol". Test codes in lab_results are short uppercase codes.
# Direct .upper() covers most (hba1c -> HBA1C, egfr -> EGFR); this covers the
# ones that don't.
LAB_CODE_ALIASES = {
    "cholesterol": "CHOL",
    "creatinine": "CREAT",
    "glucose": "GLU",
    "hemoglobin": "HGB",
}

# Phase 1's vitals.<name> convention -> vital_signs table's actual column.
VITALS_COLUMN_MAP = {
    "spo2": "oxygen_saturation",
    "temperature_c": "temperature",
    "systolic_bp": "systolic_bp",
    "diastolic_bp": "diastolic_bp",
    "heart_rate": "heart_rate",
    "height_cm": "height",
    "weight_kg": "weight",
    "respiratory_rate": "respiratory_rate",
}


def _parse_stored_value(raw):
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def _stringify(value):
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return None if value is None else str(value)


def resolve_db_field(client, patient_id: str, field: str):
    """Returns (value, found, source_lab_result_id). found=False means the
    patient has no data for this field -- must surface as 'unknown', never
    a guess. source_lab_result_id is set only for lab.* fields, citing the
    exact lab_results row the value came from."""
    if field == "age":
        res = client.table("patients").select("age").eq("patient_id", patient_id).limit(1).execute()
        if not res.data:
            return None, False, None
        return res.data[0]["age"], True, None

    if field == "sex":
        res = client.table("patients").select("gender").eq("patient_id", patient_id).limit(1).execute()
        if not res.data:
            return None, False, None
        return res.data[0]["gender"], True, None

    if field == "diagnosis.icd10":
        res = client.table("diagnoses").select("diagnosis_code").eq("patient_id", patient_id).execute()
        return [r["diagnosis_code"] for r in res.data], True, None

    if field == "diagnosis.label":
        res = client.table("diagnoses").select("diagnosis_name").eq("patient_id", patient_id).execute()
        return [r["diagnosis_name"] for r in res.data], True, None

    if field == "medication":
        res = client.table("medications").select("drug_name").eq("patient_id", patient_id).execute()
        return [r["drug_name"].lower() for r in res.data], True, None

    if field.startswith("lab."):
        name = field[len("lab."):].strip().lower()
        test_code = LAB_CODE_ALIASES.get(name, name.upper())
        res = (
            client.table("lab_results")
            .select("lab_result_id, value, test_date")
            .eq("patient_id", patient_id)
            .eq("test_code", test_code)
            .order("test_date", desc=True)
            .limit(1)
            .execute()
        )
        if not res.data:
            return None, False, None
        row = res.data[0]
        return row["value"], True, row["lab_result_id"]

    if field.startswith("vitals."):
        name = field[len("vitals."):].strip()
        column = VITALS_COLUMN_MAP.get(name)
        if column is None:
            return None, False, None
        res = (
            client.table("vital_signs")
            .select(f"{column}, measurement_date")
            .eq("patient_id", patient_id)
            .order("measurement_date", desc=True)
            .limit(1)
            .execute()
        )
        if not res.data or res.data[0][column] is None:
            return None, False, None
        return res.data[0][column], True, None

    return None, False, None


def evaluate_db_criterion(client, patient_id: str, criterion_row: dict):
    """Returns (CriterionMatch, source_lab_result_id)."""
    criterion_id = str(criterion_row["criterion_id"])
    ctype = criterion_row["type"]
    text = criterion_row.get("raw_text") or ""
    field = criterion_row.get("field")

    if criterion_row.get("needs_review"):
        return (
            CriterionMatch(
                id=criterion_id,
                type=ctype,
                text=text,
                field=field,
                verdict="unknown",
                reason="Criterion could not be structured; needs human review.",
            ),
            None,
        )

    operator = criterion_row.get("operator")
    value = _parse_stored_value(criterion_row.get("value"))

    if not field or not operator or value is None:
        return (
            CriterionMatch(
                id=criterion_id,
                type=ctype,
                text=text,
                field=field,
                verdict="unknown",
                reason="Criterion is missing field/operator/value.",
            ),
            None,
        )

    patient_value, found, source_lab_result_id = resolve_db_field(client, patient_id, field)

    if not found:
        return (
            CriterionMatch(
                id=criterion_id,
                type=ctype,
                text=text,
                field=field,
                verdict="unknown",
                reason=f"Patient has no data for '{field}'.",
            ),
            None,
        )

    op = _normalize_op(operator)
    condition_true, error = _evaluate_condition(patient_value, op, value)
    if error:
        return (
            CriterionMatch(
                id=criterion_id,
                type=ctype,
                text=text,
                field=field,
                verdict="unknown",
                patient_value=patient_value,
                reason=error,
            ),
            source_lab_result_id,
        )

    if ctype == "inclusion":
        verdict = "pass" if condition_true else "fail"
    else:
        verdict = "fail" if condition_true else "pass"

    reason = (
        f"{field} = {patient_value!r} "
        f"{'meets' if condition_true else 'does not meet'} "
        f"'{operator} {value}'"
    )
    return (
        CriterionMatch(
            id=criterion_id,
            type=ctype,
            text=text,
            field=field,
            verdict=verdict,
            patient_value=patient_value,
            reason=reason,
        ),
        source_lab_result_id,
    )


def match_patient_db(client, patient_id: str, nct_id: str):
    """Evaluates every trial_criteria row for nct_id against a Supabase
    patient, writes the results into match_results (delete-then-insert for
    this patient+trial), and returns (overall, results)."""
    criteria_res = client.table("trial_criteria").select("*").eq("nct_id", nct_id).execute()
    criteria_rows = criteria_res.data

    results = []
    sources = []
    for row in criteria_rows:
        match, source = evaluate_db_criterion(client, patient_id, row)
        results.append(match)
        sources.append(source)

    if any(r.verdict == "fail" for r in results):
        overall = "ineligible"
    elif any(r.verdict == "unknown" for r in results):
        overall = "needs more data"
    else:
        overall = "eligible"

    client.table("match_results").delete().eq("patient_id", patient_id).eq("nct_id", nct_id).execute()

    match_rows = [
        {
            "patient_id": patient_id,
            "nct_id": nct_id,
            "criterion_id": int(r.id),
            "verdict": r.verdict,
            "patient_value_used": _stringify(r.patient_value),
            "source_lab_result_id": source,
            "reason": r.reason,
        }
        for r, source in zip(results, sources)
    ]
    if match_rows:
        client.table("match_results").insert(match_rows).execute()

    return overall, results
