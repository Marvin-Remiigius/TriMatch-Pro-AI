"""
Coarse SQL filter -- narrows the 1000-patient pool down to patients that
*could* still pass a trial's criteria, using indexed SQL queries, before the
expensive per-criterion deterministic evaluation runs on the survivors.

This only ever narrows the pool safely: a criterion only removes a patient
here if the full matcher would deterministically fail them on that exact
criterion too (never "unknown" -- age and required-diagnosis checks always
resolve to a known value). Under-filtering (not narrowing on a criterion
type this module doesn't handle) is always safe, just less efficient --
those criteria still get evaluated correctly by the full matcher afterward.
Over-filtering would be a real bug, so this stays conservative: only age
range/equality and "must have this diagnosis" are handled.
"""

from db_matching import _parse_stored_value
from matching import _is_numeric

_INVERT_OP = {">": "<=", ">=": "<", "<": ">=", "<=": ">", "==": "!="}


def _apply_numeric_op(query, column: str, operator: str, value: float):
    if operator == ">":
        return query.gt(column, value)
    if operator == ">=":
        return query.gte(column, value)
    if operator == "<":
        return query.lt(column, value)
    if operator == "<=":
        return query.lte(column, value)
    if operator == "==":
        return query.eq(column, value)
    if operator == "!=":
        return query.neq(column, value)
    return query


def _safe_ids_for_age(client, row: dict):
    value = _parse_stored_value(row.get("value"))
    operator = row.get("operator")
    if not _is_numeric(value) or operator not in (">", ">=", "<", "<=", "=="):
        return None

    op = operator if row["type"] == "inclusion" else _INVERT_OP.get(operator)
    if op is None:
        return None

    # patients.age is a Postgres integer column -- passing a float (e.g.
    # 18.0) serializes as "18.0" and Postgres rejects that for an int column.
    query = _apply_numeric_op(client.table("patients").select("patient_id"), "age", op, int(float(value)))
    res = query.limit(2000).execute()
    return {r["patient_id"] for r in res.data}


def _safe_ids_for_required_diagnosis(client, row: dict):
    """Only handles the common, unambiguous case: an inclusion criterion
    requiring a specific diagnosis code. Exclusion-diagnosis and
    not_contains/not_in are left to the full matcher (not narrowed here)."""
    if row["type"] != "inclusion" or row.get("operator") not in ("contains", "in", "=="):
        return None

    value = _parse_stored_value(row.get("value"))
    codes = value if isinstance(value, list) else [value]
    codes = [c for c in codes if isinstance(c, str)]
    if not codes:
        return None

    res = client.table("diagnoses").select("patient_id").in_("diagnosis_code", codes).limit(5000).execute()
    return {r["patient_id"] for r in res.data}


def coarse_filter_patient_ids(client, criteria_rows: list[dict]):
    """Returns a set of patient_ids not definitively ruled out by any
    coarse-filterable criterion, or None if none of the criteria were
    coarse-filterable (caller should fall back to the full patient list)."""
    safe_sets = []
    for row in criteria_rows:
        if row.get("needs_review"):
            continue
        field = row.get("field")
        if field == "age":
            s = _safe_ids_for_age(client, row)
        elif field == "diagnosis.icd10":
            s = _safe_ids_for_required_diagnosis(client, row)
        else:
            s = None
        if s is not None:
            safe_sets.append(s)

    if not safe_sets:
        return None

    result = safe_sets[0]
    for s in safe_sets[1:]:
        result &= s
    return result
