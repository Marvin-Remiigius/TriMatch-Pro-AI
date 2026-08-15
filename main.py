import json

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from audit import get_audit_log, get_flagged_for_review, log_match_results
from coarse_filter import coarse_filter_patient_ids
from db import get_client
from db_matching import match_patient_db
from llm import parse_criteria
from matching import match_patient
from models import (
    AuditEntry,
    Candidate,
    CandidateListResponse,
    DBCandidateListResponse,
    DBCandidateSummary,
    ImportTrialResponse,
    MatchRequest,
    MatchResponse,
    ParseCriteriaRequest,
    ParseCriteriaResponse,
    ParseCriteriaToDBResponse,
    Patient,
    TrialCriterionRow,
)
from patients import get_patient, load_patients
from trials import fetch_trial, to_trial_row

load_dotenv()

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/trials/{nct_id}")
async def get_trial(nct_id: str):
    return await fetch_trial(nct_id)


@app.post("/trials/{nct_id}/import", response_model=ImportTrialResponse)
async def import_trial(nct_id: str):
    trial = await fetch_trial(nct_id)
    row = to_trial_row(trial)

    client = get_client()
    client.table("trials").upsert(row, on_conflict="nct_id").execute()

    return ImportTrialResponse(
        **row, eligibility_criteria=trial.get("eligibility_criteria")
    )


@app.post("/trials/{nct_id}/parse-criteria", response_model=ParseCriteriaToDBResponse)
async def parse_trial_criteria(nct_id: str):
    trial = await fetch_trial(nct_id)
    eligibility_text = trial.get("eligibility_criteria")
    if not eligibility_text:
        raise HTTPException(
            status_code=422, detail=f"Trial {nct_id} has no eligibility criteria text"
        )

    client = get_client()
    client.table("trials").upsert(to_trial_row(trial), on_conflict="nct_id").execute()

    criteria = parse_criteria(eligibility_text)

    client.table("trial_criteria").delete().eq("nct_id", nct_id).execute()

    rows = [
        {
            "nct_id": nct_id,
            "type": c.type,
            "raw_text": c.text,
            "field": c.field,
            "operator": c.operator,
            "value": json.dumps(c.value) if c.value is not None else None,
            "unit": c.unit,
            "needs_review": c.needs_review,
        }
        for c in criteria
    ]
    inserted = client.table("trial_criteria").insert(rows).execute()

    return ParseCriteriaToDBResponse(
        nct_id=nct_id,
        total=len(criteria),
        inclusion=sum(1 for c in criteria if c.type == "inclusion"),
        exclusion=sum(1 for c in criteria if c.type == "exclusion"),
        needs_review=sum(1 for c in criteria if c.needs_review),
        criteria=[TrialCriterionRow(**row) for row in inserted.data],
    )


@app.post("/trials/{nct_id}/match/{patient_id}", response_model=MatchResponse)
def match_db_patient(nct_id: str, patient_id: str):
    client = get_client()

    patient_check = (
        client.table("patients").select("patient_id").eq("patient_id", patient_id).limit(1).execute()
    )
    if not patient_check.data:
        raise HTTPException(status_code=404, detail=f"Patient {patient_id} not found")

    criteria_rows = client.table("trial_criteria").select("*").eq("nct_id", nct_id).execute().data
    if not criteria_rows:
        raise HTTPException(
            status_code=404,
            detail=f"No parsed criteria for trial {nct_id} -- call POST /trials/{nct_id}/parse-criteria first",
        )

    overall, results = match_patient_db(client, patient_id, nct_id, criteria_rows=criteria_rows)
    return MatchResponse(patient_id=patient_id, overall=overall, results=results)


_MAX_EVALUATE_DEFAULT = 200


@app.get("/trials/{nct_id}/db-candidates", response_model=DBCandidateListResponse)
def get_db_candidates(nct_id: str, limit: int = 50, max_evaluate: int = _MAX_EVALUATE_DEFAULT):
    client = get_client()

    criteria_rows = client.table("trial_criteria").select("*").eq("nct_id", nct_id).execute().data
    if not criteria_rows:
        raise HTTPException(
            status_code=404,
            detail=f"No parsed criteria for trial {nct_id} -- call POST /trials/{nct_id}/parse-criteria first",
        )

    total_patients = (
        client.table("patients").select("patient_id", count="exact").limit(0).execute().count
    )

    safe_ids = coarse_filter_patient_ids(client, criteria_rows)
    if safe_ids is None:
        all_ids = client.table("patients").select("patient_id").limit(2000).execute().data
        candidate_ids = sorted(r["patient_id"] for r in all_ids)
    else:
        candidate_ids = sorted(safe_ids)

    coarse_filtered_count = len(candidate_ids)
    to_evaluate = candidate_ids[:max_evaluate]

    candidates = []
    for pid in to_evaluate:
        overall, results = match_patient_db(client, pid, nct_id, criteria_rows=criteria_rows)
        candidates.append(
            DBCandidateSummary(
                patient_id=pid,
                overall=overall,
                pass_count=sum(1 for r in results if r.verdict == "pass"),
                fail_count=sum(1 for r in results if r.verdict == "fail"),
                unknown_count=sum(1 for r in results if r.verdict == "unknown"),
            )
        )

    candidates.sort(
        key=lambda c: (_OVERALL_RANK[c.overall], -c.pass_count, c.unknown_count, c.fail_count)
    )

    return DBCandidateListResponse(
        nct_id=nct_id,
        total_patients=total_patients,
        coarse_filtered_count=coarse_filtered_count,
        evaluated_count=len(candidates),
        returned=min(limit, len(candidates)),
        candidates=candidates[:limit],
    )


@app.get("/patients", response_model=list[Patient])
def list_patients():
    return load_patients()


@app.get("/patients/{patient_id}", response_model=Patient)
def get_patient_by_id(patient_id: str):
    patient = get_patient(patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail=f"Patient {patient_id} not found")
    return patient


@app.post("/parse-criteria", response_model=ParseCriteriaResponse)
def parse_criteria_endpoint(request: ParseCriteriaRequest):
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="text must not be empty")
    criteria = parse_criteria(request.text)
    return ParseCriteriaResponse(criteria=criteria)


@app.post("/match", response_model=MatchResponse)
def match_endpoint(request: MatchRequest):
    patient = get_patient(request.patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail=f"Patient {request.patient_id} not found")
    overall, results = match_patient(patient, request.criteria)
    log_match_results(patient.id, results, nct_id=request.nct_id)
    return MatchResponse(patient_id=patient.id, overall=overall, results=results)


_OVERALL_RANK = {"eligible": 0, "needs more data": 1, "ineligible": 2}


@app.get("/trials/{nct_id}/candidates", response_model=CandidateListResponse)
async def get_candidates(nct_id: str):
    trial = await fetch_trial(nct_id)
    eligibility_text = trial.get("eligibility_criteria")
    if not eligibility_text:
        raise HTTPException(
            status_code=422, detail=f"Trial {nct_id} has no eligibility criteria text"
        )
    criteria = parse_criteria(eligibility_text)

    candidates = []
    for patient in load_patients():
        overall, results = match_patient(patient, criteria)
        log_match_results(patient.id, results, nct_id=nct_id)
        candidates.append(
            Candidate(
                patient_id=patient.id,
                overall=overall,
                pass_count=sum(1 for r in results if r.verdict == "pass"),
                fail_count=sum(1 for r in results if r.verdict == "fail"),
                unknown_count=sum(1 for r in results if r.verdict == "unknown"),
                results=results,
            )
        )

    candidates.sort(
        key=lambda c: (_OVERALL_RANK[c.overall], -c.pass_count, c.unknown_count, c.fail_count)
    )

    return CandidateListResponse(
        nct_id=trial["nct_id"], title=trial["title"], criteria=criteria, candidates=candidates
    )


@app.get("/audit-log", response_model=list[AuditEntry])
def audit_log_endpoint(
    nct_id: str | None = None, patient_id: str | None = None, limit: int | None = None
):
    return get_audit_log(nct_id=nct_id, patient_id=patient_id, limit=limit)


@app.get("/flagged-for-review", response_model=list[AuditEntry])
def flagged_for_review_endpoint(
    nct_id: str | None = None, patient_id: str | None = None, limit: int | None = None
):
    return get_flagged_for_review(nct_id=nct_id, patient_id=patient_id, limit=limit)


app.mount("/", StaticFiles(directory="static", html=True), name="static")
