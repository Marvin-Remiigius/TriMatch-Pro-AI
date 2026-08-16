import json

from matching import _evaluate_condition, _normalize_op
from models import CriterionMatch, RuleResult

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


def _is_rule_list(value) -> bool:
    """True when a stored `value` is a JSON-encoded list of sub-rule dicts
    (the new multi-rule encoding) rather than a plain scalar/list value
    (the existing single-rule encoding, e.g. ["male","female"] for an
    "in" operator -- distinguished by each item being a dict with a
    "field" key, which a plain value list never has)."""
    return isinstance(value, list) and len(value) > 0 and all(
        isinstance(v, dict) and "field" in v for v in value
    )


def _evaluate_single_db_rule(client, patient_id: str, field, operator, value) -> dict:
    """Evaluates one sub-rule against a Supabase patient. Mirrors
    matching._evaluate_single_rule but resolves fields via Supabase and
    also tracks the source_lab_result_id citation."""
    if not field or not operator or value is None:
        return {
            "status": "unknown",
            "condition_true": None,
            "patient_value": None,
            "source_lab_result_id": None,
            "reason": "rule is missing field/operator/value",
        }

    patient_value, found, source_lab_result_id = resolve_db_field(client, patient_id, field)

    if not found:
        return {
            "status": "unknown",
            "condition_true": None,
            "patient_value": None,
            "source_lab_result_id": None,
            "reason": f"Patient has no data for '{field}'.",
        }

    op = _normalize_op(operator)
    condition_true, error = _evaluate_condition(patient_value, op, value)
    if error:
        return {
            "status": "unknown",
            "condition_true": None,
            "patient_value": patient_value,
            "source_lab_result_id": source_lab_result_id,
            "reason": error,
        }

    reason = (
        f"{field} = {patient_value!r} "
        f"{'meets' if condition_true else 'does not meet'} "
        f"'{operator} {value}'"
    )
    return {
        "status": "known",
        "condition_true": condition_true,
        "patient_value": patient_value,
        "source_lab_result_id": source_lab_result_id,
        "reason": reason,
    }


def _effective_db_rules(criterion_row: dict) -> list:
    """Returns a list of {"field","operator","value","unit"} dicts to
    evaluate. If the stored `value` decodes to a rule list, uses that
    (new multi-rule case). Otherwise synthesizes a single legacy rule from
    the top-level field/operator/value columns -- ONLY when all three are
    present, matching the old gate exactly so criteria stored before this
    change evaluate identically to before."""
    field = criterion_row.get("field")
    operator = criterion_row.get("operator")
    value = _parse_stored_value(criterion_row.get("value"))
    unit = criterion_row.get("unit")

    if _is_rule_list(value):
        return value

    if field and operator and value is not None:
        return [{"field": field, "operator": operator, "value": value, "unit": unit}]

    return []


def evaluate_db_criterion(client, patient_id: str, criterion_row: dict) -> CriterionMatch:
    criterion_id = str(criterion_row["criterion_id"])
    ctype = criterion_row["type"]
    text = criterion_row.get("raw_text") or ""
    field = criterion_row.get("field")

    rules = _effective_db_rules(criterion_row)

    if criterion_row.get("needs_review") and not rules:
        return CriterionMatch(
            id=criterion_id,
            type=ctype,
            text=text,
            field=field,
            verdict="unknown",
            reason="Criterion could not be structured; needs human review.",
        )

    if not rules:
        return CriterionMatch(
            id=criterion_id,
            type=ctype,
            text=text,
            field=field,
            verdict="unknown",
            reason="Criterion is missing field/operator/value.",
        )

    evaluations = [
        _evaluate_single_db_rule(client, patient_id, r.get("field"), r.get("operator"), r.get("value"))
        for r in rules
    ]

    # Same Kleene-AND combination as the in-memory matcher: a known-false
    # rule makes the whole compound condition false regardless of other
    # unknowns; otherwise any unknown makes the combined result unknown;
    # only all-known-true is true.
    if any(e["status"] == "known" and e["condition_true"] is False for e in evaluations):
        combined_true = False
    elif any(e["status"] == "unknown" for e in evaluations):
        combined_true = None
    else:
        combined_true = True

    if combined_true is None:
        verdict = "unknown"
    elif ctype == "inclusion":
        verdict = "pass" if combined_true else "fail"
    else:
        verdict = "fail" if combined_true else "pass"

    # Same partial-criterion honesty guard as the in-memory matcher.
    partial_note = None
    if criterion_row.get("needs_review") and rules and verdict == "pass":
        verdict = "unknown"
        partial_note = "this criterion is only partially structured; full compliance not confirmed"

    if len(rules) == 1:
        reason = evaluations[0]["reason"]
        patient_value = evaluations[0]["patient_value"]
        source_lab_result_id = evaluations[0]["source_lab_result_id"]
    else:
        reason = "; ".join(e["reason"] for e in evaluations)
        patient_value = [e["patient_value"] for e in evaluations]
        source_lab_result_id = next(
            (e["source_lab_result_id"] for e in evaluations if e["source_lab_result_id"]), None
        )

    if partial_note:
        reason = f"{reason} — {partial_note}"

    rule_results = None
    if len(rules) > 1:
        rule_results = [
            RuleResult(
                field=r.get("field"),
                operator=r.get("operator"),
                value=r.get("value"),
                patient_value=e["patient_value"],
                condition_met=e["condition_true"],
                source_lab_result_id=e["source_lab_result_id"],
                reason=e["reason"],
            )
            for r, e in zip(rules, evaluations)
        ]

    return CriterionMatch(
        id=criterion_id,
        type=ctype,
        text=text,
        field=field,
        verdict=verdict,
        patient_value=patient_value,
        source_lab_result_id=source_lab_result_id,
        reason=reason,
        rule_results=rule_results,
    )


def match_patient_db(client, patient_id: str, nct_id: str, criteria_rows=None):
    """Evaluates every trial_criteria row for nct_id against a Supabase
    patient, writes the results into match_results (delete-then-insert for
    this patient+trial), and returns (overall, results). Pass criteria_rows
    when matching many patients against the same trial in one call, so each
    patient doesn't re-fetch the identical criteria list."""
    if criteria_rows is None:
        criteria_res = client.table("trial_criteria").select("*").eq("nct_id", nct_id).execute()
        criteria_rows = criteria_res.data

    results = [evaluate_db_criterion(client, patient_id, row) for row in criteria_rows]

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
            "source_lab_result_id": r.source_lab_result_id,
            "reason": r.reason,
        }
        for r in results
    ]
    if match_rows:
        client.table("match_results").insert(match_rows).execute()

    return overall, results
