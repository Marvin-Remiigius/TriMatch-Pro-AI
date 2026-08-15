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
