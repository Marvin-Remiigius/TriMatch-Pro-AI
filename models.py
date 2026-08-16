from datetime import date, datetime
from typing import Literal, Optional, Union

from pydantic import BaseModel, model_validator


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


class Rule(BaseModel):
    field: Optional[str] = None
    operator: Optional[str] = None
    value: Optional[Union[float, str, bool, list]] = None
    unit: Optional[str] = None


class Criterion(BaseModel):
    id: str
    type: Literal["inclusion", "exclusion"]
    text: str
    field: Optional[str] = None
    operator: Optional[str] = None
    value: Optional[Union[float, str, bool, list]] = None
    unit: Optional[str] = None
    # New, additive: a criterion may decompose into several AND'd sub-rules.
    # When absent/empty, callers fall back to the single field/operator/value
    # above -- existing single-rule behavior is completely unchanged.
    rules: Optional[list[Rule]] = None
    # New, additive: alternative pathways to satisfy ONE criterion (e.g. a
    # parent bullet's requirement met via any one of several child bullets,
    # or a single-sentence "X, and/or Y"). Each inner list is AND'd
    # internally (same semantics as `rules`); the outer list is OR'd --
    # satisfying any one group is enough. Mutually exclusive with `rules`;
    # when absent, callers fall back to `rules` -- existing behavior for
    # every criterion that doesn't use this is completely unchanged.
    rule_groups: Optional[list[list[Rule]]] = None
    needs_review: bool = False
    reason: Optional[str] = None

    @model_validator(mode="after")
    def _mirror_first_rule(self):
        # Keeps the legacy top-level field/operator/value/unit populated
        # (from rule_groups[0][0], or rules[0]) for anything still reading
        # them directly, so criteria parsed under the newer schemas stay
        # fully backward compatible with old consumers.
        if self.field is None and self.operator is None and self.value is None:
            first = None
            if self.rule_groups and self.rule_groups[0]:
                first = self.rule_groups[0][0]
            elif self.rules:
                first = self.rules[0]
            if first is not None:
                self.field = first.field
                self.operator = first.operator
                self.value = first.value
                self.unit = first.unit
        return self


class ParseCriteriaRequest(BaseModel):
    text: str


class ParseCriteriaResponse(BaseModel):
    criteria: list[Criterion]


class RuleResult(BaseModel):
    field: Optional[str] = None
    operator: Optional[str] = None
    value: Optional[Union[float, str, bool, list]] = None
    patient_value: Optional[Union[float, str, bool, list]] = None
    condition_met: Optional[bool] = None  # None = unknown (missing data)
    source_lab_result_id: Optional[int] = None
    reason: str
    # New, additive: which OR'd alternative-pathway group this rule belongs
    # to (0-based), only set when the criterion used rule_groups with more
    # than one group. None for the plain flat-AND case -- unchanged.
    group: Optional[int] = None


class CriterionMatch(BaseModel):
    id: str
    type: Literal["inclusion", "exclusion"]
    text: str
    field: Optional[str] = None
    verdict: Literal["pass", "fail", "unknown"]
    patient_value: Optional[Union[float, str, bool, list]] = None
    source_lab_result_id: Optional[int] = None
    reason: str
    # New, additive: per-sub-rule breakdown when the criterion decomposed
    # into multiple rules. None/absent for existing single-rule criteria --
    # the fields above still carry a sensible summary either way.
    rule_results: Optional[list[RuleResult]] = None


class MatchRequest(BaseModel):
    patient_id: str
    criteria: list[Criterion]
    nct_id: Optional[str] = None


class MatchResponse(BaseModel):
    patient_id: str
    overall: Literal["eligible", "ineligible", "needs more data"]
    results: list[CriterionMatch]


class Candidate(BaseModel):
    patient_id: str
    overall: Literal["eligible", "ineligible", "needs more data"]
    pass_count: int
    fail_count: int
    unknown_count: int
    results: list[CriterionMatch]


class CandidateListResponse(BaseModel):
    nct_id: str
    title: Optional[str] = None
    criteria: list[Criterion]
    candidates: list[Candidate]


class AuditEntry(BaseModel):
    timestamp: datetime
    nct_id: Optional[str] = None
    patient_id: str
    criterion_id: str
    field: Optional[str] = None
    verdict: Literal["pass", "fail", "unknown"]
    patient_value: Optional[Union[float, str, bool, list]] = None
    reason: str


class ImportTrialResponse(BaseModel):
    nct_id: str
    title: Optional[str] = None
    phase: Optional[str] = None
    status: Optional[str] = None
    primary_endpoint: Optional[str] = None
    eligibility_criteria: Optional[str] = None


class TrialCriterionRow(BaseModel):
    criterion_id: Optional[int] = None
    nct_id: str
    type: Literal["inclusion", "exclusion"]
    raw_text: str
    field: Optional[str] = None
    operator: Optional[str] = None
    value: Optional[str] = None
    unit: Optional[str] = None
    needs_review: bool = False


class ParseCriteriaToDBResponse(BaseModel):
    nct_id: str
    total: int
    inclusion: int
    exclusion: int
    needs_review: int
    criteria: list[TrialCriterionRow]


class DBCandidateSummary(BaseModel):
    patient_id: str
    age: Optional[int] = None
    sex: Optional[str] = None
    overall: Literal["eligible", "ineligible", "needs more data"]
    pass_count: int
    fail_count: int
    unknown_count: int


class DBCandidateListResponse(BaseModel):
    nct_id: str
    total_patients: int
    coarse_filtered_count: int
    evaluated_count: int
    returned: int
    candidates: list[DBCandidateSummary]


class TestProgress(BaseModel):
    test_code: str
    test_name: str
    unit: Optional[str] = None
    baseline_value: Optional[float] = None
    baseline_date: Optional[str] = None
    baseline_lab_result_id: Optional[int] = None
    latest_value: Optional[float] = None
    latest_date: Optional[str] = None
    latest_lab_result_id: Optional[int] = None
    deviation: Optional[float] = None
    status: Literal["improved", "worsened", "indeterminate", "no_data"]


class PatientProgress(BaseModel):
    patient_id: str
    status: str
    baseline_date: Optional[str] = None
    tests: list[TestProgress]


class TrialProgressResponse(BaseModel):
    nct_id: str
    enrolled: int
    active: int
    dropouts: int
    success_rate: Optional[float] = None
    primary_test_code_used: Optional[str] = None
    improved_count: int
    patients: list[PatientProgress]


class EnrollmentRecord(BaseModel):
    patient_id: str
    nct_id: str
    status: str
    baseline_date: Optional[str] = None
    invited_at: Optional[str] = None
    consented_at: Optional[str] = None
    enrolled_at: Optional[str] = None
    withdrawn_at: Optional[str] = None
    updated_at: Optional[str] = None


class AuditLogRow(BaseModel):
    event_id: int
    actor: Optional[str] = None
    action: str
    entity: str
    entity_id: str
    source_ref: Optional[str] = None
    detail: Optional[dict] = None
    created_at: str
