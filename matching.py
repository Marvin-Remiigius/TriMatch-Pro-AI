from models import Criterion, CriterionMatch, Patient, Rule, RuleResult

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


def _evaluate_single_rule(patient: Patient, rule: Rule) -> dict:
    """Evaluates one sub-rule against a patient. Returns a dict with
    status ('known'|'unknown'), condition_true (bool|None), patient_value,
    and reason -- the same per-field logic the single-rule matcher always
    used, just parametrized on a Rule instead of a Criterion."""
    if not rule.field or not rule.operator or rule.value is None:
        return {
            "status": "unknown",
            "condition_true": None,
            "patient_value": None,
            "reason": "rule is missing field/operator/value",
        }

    operator = _normalize_op(rule.operator)
    patient_value, found = _resolve_field(patient, rule.field)

    if not found:
        return {
            "status": "unknown",
            "condition_true": None,
            "patient_value": None,
            "reason": f"Patient has no data for '{rule.field}'.",
        }

    condition_true, error = _evaluate_condition(patient_value, operator, rule.value)
    if error:
        return {
            "status": "unknown",
            "condition_true": None,
            "patient_value": patient_value,
            "reason": error,
        }

    reason = (
        f"{rule.field} = {patient_value!r} "
        f"{'meets' if condition_true else 'does not meet'} "
        f"'{rule.operator} {rule.value}'"
    )
    return {
        "status": "known",
        "condition_true": condition_true,
        "patient_value": patient_value,
        "reason": reason,
    }


def _effective_rules(criterion: Criterion) -> list[Rule]:
    """Returns criterion.rules if present; otherwise synthesizes a single
    legacy rule from the top-level field/operator/value -- ONLY when all
    three are present, matching the old gate exactly so criteria parsed
    before this change (or by anything not yet emitting `rules`) evaluate
    identically to before."""
    if criterion.rules:
        return criterion.rules
    if criterion.field and criterion.operator and criterion.value is not None:
        return [Rule(field=criterion.field, operator=criterion.operator, value=criterion.value, unit=criterion.unit)]
    return []


def evaluate_criterion(criterion: Criterion, patient: Patient) -> CriterionMatch:
    rules = _effective_rules(criterion)

    if criterion.needs_review and not rules:
        return CriterionMatch(
            id=criterion.id,
            type=criterion.type,
            text=criterion.text,
            field=criterion.field,
            verdict="unknown",
            reason=criterion.reason or "Criterion could not be structured; needs human review.",
        )

    if not rules:
        return CriterionMatch(
            id=criterion.id,
            type=criterion.type,
            text=criterion.text,
            field=criterion.field,
            verdict="unknown",
            reason="Criterion is missing field/operator/value.",
        )

    evaluations = [_evaluate_single_rule(patient, r) for r in rules]

    # Kleene AND across sub-rules: a known-false rule makes the whole
    # compound condition false regardless of any unknowns (False AND
    # anything is False); otherwise any unknown rule makes the combined
    # result unknown; only when every rule is known-true is it true.
    if any(e["status"] == "known" and e["condition_true"] is False for e in evaluations):
        combined_true = False
    elif any(e["status"] == "unknown" for e in evaluations):
        combined_true = None
    else:
        combined_true = True

    # Inclusion: combined condition true means the patient meets the
    # requirement (pass). Exclusion: combined condition true means the
    # disqualifying condition is present, which blocks the patient (fail)
    # -- so the sense is inverted. Same rule as before, applied once to
    # the combined result rather than per-rule.
    if combined_true is None:
        verdict = "unknown"
    elif criterion.type == "inclusion":
        verdict = "pass" if combined_true else "fail"
    else:
        verdict = "fail" if combined_true else "pass"

    # Honesty guard for partially-structured criteria: needs_review can now
    # stay true even when some rules were extracted (a genuinely
    # unstructurable remainder exists). A "fail" from the checked part is
    # still valid (AND with anything false is false), but a "pass" only
    # confirms the checked part, not the whole original criterion -- don't
    # let that read as a full pass.
    partial_note = None
    if criterion.needs_review and rules and verdict == "pass":
        verdict = "unknown"
        partial_note = (
            "this criterion is only partially structured "
            f"({criterion.reason or 'part could not be reliably parsed'}); "
            "full compliance not confirmed"
        )

    if len(rules) == 1:
        reason = evaluations[0]["reason"]
        patient_value = evaluations[0]["patient_value"]
    else:
        reason = "; ".join(e["reason"] for e in evaluations)
        patient_value = [e["patient_value"] for e in evaluations]

    if partial_note:
        reason = f"{reason} — {partial_note}"

    rule_results = None
    if criterion.rules:
        rule_results = [
            RuleResult(
                field=r.field,
                operator=r.operator,
                value=r.value,
                patient_value=e["patient_value"],
                condition_met=e["condition_true"],
                reason=e["reason"],
            )
            for r, e in zip(rules, evaluations)
        ]

    return CriterionMatch(
        id=criterion.id,
        type=criterion.type,
        text=criterion.text,
        field=criterion.field,
        verdict=verdict,
        patient_value=patient_value,
        reason=reason,
        rule_results=rule_results,
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
