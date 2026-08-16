from datetime import date, datetime
from typing import Literal, Optional, Union

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


class Criterion(BaseModel):
    id: str
    type: Literal["inclusion", "exclusion"]
    text: str
    field: Optional[str] = None
    operator: Optional[str] = None
    value: Optional[Union[float, str, bool, list]] = None
    unit: Optional[str] = None
    needs_review: bool = False
    reason: Optional[str] = None


class ParseCriteriaRequest(BaseModel):
    text: str


class ParseCriteriaResponse(BaseModel):
    criteria: list[Criterion]


class CriterionMatch(BaseModel):
    id: str
    type: Literal["inclusion", "exclusion"]
    text: str
    field: Optional[str] = None
    verdict: Literal["pass", "fail", "unknown"]
    patient_value: Optional[Union[float, str, bool, list]] = None
    source_lab_result_id: Optional[int] = None
    reason: str


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
