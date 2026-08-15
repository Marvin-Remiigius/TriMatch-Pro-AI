from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from llm import parse_criteria
from matching import match_patient
from models import (
    Candidate,
    CandidateListResponse,
    MatchRequest,
    MatchResponse,
    ParseCriteriaRequest,
    ParseCriteriaResponse,
    Patient,
)
from patients import get_patient, load_patients
from trials import fetch_trial

load_dotenv()

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/trials/{nct_id}")
async def get_trial(nct_id: str):
    return await fetch_trial(nct_id)


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


app.mount("/", StaticFiles(directory="static", html=True), name="static")
