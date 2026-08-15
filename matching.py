from models import Criterion, CriterionMatch, Patient

_OP_ALIASES = {
    "=": "==",
    "eq": "==",
    "equals": "==",
    "ne": "!=",
    "<>": "!=",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
}


def _normalize_op(operator: str) -> str:
    operator = operator.strip().lower()
    return _OP_ALIASES.get(operator, operator)


def _resolve_field(patient: Patient, field: str):
    """Returns (value, found). found=False means the patient record has no
    data for this field, which must surface as 'unknown', never a guess."""
    if field == "age":
        return patient.age, True
    if field == "sex":
        return patient.sex, True
    if field == "diagnosis.icd10":
        return [d.icd10 for d in patient.diagnoses], True
    if field == "diagnosis.label":
        return [d.label for d in patient.diagnoses], True
    if field == "medication":
        return [m.lower() for m in patient.medications], True
    if field.startswith("lab."):
        lab_name = field[len("lab."):].replace("_", " ").strip().lower()
        matches = [
            lab for lab in patient.labs
            if lab.name.replace("_", " ").strip().lower() == lab_name
        ]
        if not matches:
            return None, False
        latest = max(matches, key=lambda lab: lab.date)
        return latest.value, True
    if field.startswith("vitals."):
        vital_name = field[len("vitals."):].strip()
        if not hasattr(patient.vitals, vital_name):
            return None, False
        value = getattr(patient.vitals, vital_name)
        return value, value is not None
    return None, False


def _is_numeric(value) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        try:
            float(value)
            return True
        except ValueError:
            return False
    return False


def _to_list(value) -> list:
    return value if isinstance(value, list) else [value]


def _evaluate_condition(patient_value, operator: str, criterion_value):
    """Returns (result, error). error is set (result is None) when the
    comparison can't be evaluated, which becomes an 'unknown' verdict."""
    if operator in (">", ">=", "<", "<="):
        if not _is_numeric(patient_value) or not _is_numeric(criterion_value):
            return None, f"'{patient_value}' is not numeric, cannot compare"
        a, b = float(patient_value), float(criterion_value)
        if operator == ">":
            return a > b, None
        if operator == ">=":
            return a >= b, None
        if operator == "<":
            return a < b, None
        return a <= b, None

    if operator in ("==", "!="):
        if _is_numeric(patient_value) and _is_numeric(criterion_value):
            equal = float(patient_value) == float(criterion_value)
        else:
            equal = str(patient_value).strip().lower() == str(criterion_value).strip().lower()
        return (equal if operator == "==" else not equal), None

    if operator in ("contains", "not_contains"):
        haystack = _to_list(patient_value)
        needle = str(criterion_value).strip().lower()
        found = any(str(item).strip().lower() == needle for item in haystack)
        return (found if operator == "contains" else not found), None

    if operator in ("in", "not_in"):
        allowed = _to_list(criterion_value)
        needle = str(patient_value).strip().lower()
        found = any(str(item).strip().lower() == needle for item in allowed)
        return (found if operator == "in" else not found), None

    return None, f"unsupported operator '{operator}'"


def evaluate_criterion(criterion: Criterion, patient: Patient) -> CriterionMatch:
    if criterion.needs_review:
        return CriterionMatch(
            id=criterion.id,
            type=criterion.type,
            text=criterion.text,
            field=criterion.field,
            verdict="unknown",
            reason=criterion.reason or "Criterion could not be structured; needs human review.",
        )

    if not criterion.field or not criterion.operator or criterion.value is None:
        return CriterionMatch(
            id=criterion.id,
            type=criterion.type,
            text=criterion.text,
            field=criterion.field,
            verdict="unknown",
            reason="Criterion is missing field/operator/value.",
        )

    operator = _normalize_op(criterion.operator)
    patient_value, found = _resolve_field(patient, criterion.field)

    if not found:
        return CriterionMatch(
            id=criterion.id,
            type=criterion.type,
            text=criterion.text,
            field=criterion.field,
            verdict="unknown",
            reason=f"Patient has no data for '{criterion.field}'.",
        )

    condition_true, error = _evaluate_condition(patient_value, operator, criterion.value)
    if error:
        return CriterionMatch(
            id=criterion.id,
            type=criterion.type,
            text=criterion.text,
            field=criterion.field,
            verdict="unknown",
            patient_value=patient_value,
            reason=error,
        )

    # Inclusion: condition true means the patient meets the requirement (pass).
    # Exclusion: condition true means the disqualifying condition is present,
    # which blocks the patient (fail) -- so the sense is inverted.
    if criterion.type == "inclusion":
        verdict = "pass" if condition_true else "fail"
    else:
        verdict = "fail" if condition_true else "pass"

    reason = (
        f"{criterion.field} = {patient_value!r} "
        f"{'meets' if condition_true else 'does not meet'} "
        f"'{criterion.operator} {criterion.value}'"
    )
    return CriterionMatch(
        id=criterion.id,
        type=criterion.type,
        text=criterion.text,
        field=criterion.field,
        verdict=verdict,
        patient_value=patient_value,
        reason=reason,
    )


def match_patient(patient: Patient, criteria: list[Criterion]) -> tuple[str, list[CriterionMatch]]:
    results = [evaluate_criterion(c, patient) for c in criteria]

    if any(r.verdict == "fail" for r in results):
        overall = "ineligible"
    elif any(r.verdict == "unknown" for r in results):
        overall = "needs more data"
    else:
        overall = "eligible"

    return overall, results
