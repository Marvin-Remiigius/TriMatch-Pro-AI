from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from postgrest.exceptions import APIError

from audit import get_audit_log, get_flagged_for_review, log_match_results
from coarse_filter import coarse_filter_patient_ids
from db import get_client
from db_matching import match_patient_db
from document_upload import SUPPORTED_DOCUMENT_TYPES, store_patient_document
from enrollment import (
    InvalidTransitionError,
    consent_patient,
    decline_patient,
    enroll_patient,
    invite_patient,
    list_enrollment,
    list_patient_enrollment,
    list_trial_audit,
    withdraw_patient,
)
from lab_upload import ingest_lab_report, ingest_lab_report_file
from llm import SUPPORTED_DOCUMENT_FILE_MIME_TYPES, parse_criteria
from matching import match_patient
from models import (
    AuditEntry,
    AuditLogRow,
    Candidate,
    CandidateListResponse,
    DBCandidateListResponse,
    DBCandidateSummary,
    EnrollmentRecord,
    ImportTrialResponse,
    LabResultRecord,
    MatchRequest,
    MatchResponse,
    ParseCriteriaRequest,
    ParseCriteriaResponse,
    ParseCriteriaToDBResponse,
    Patient,
    PatientSignupRequest,
    PatientSignupResponse,
    TrialCriterionRow,
    TrialProgressResponse,
    UploadDocumentResponse,
    UploadLabRequest,
    UploadLabResponse,
    UploadTrialResponse,
)
from patients import get_patient, load_patients
from patient_signup import create_patient
from progress import compute_trial_progress, store_trial_metrics
from trial_upload import ingest_trial_document, ingest_trial_document_file
from trials import criterion_to_db_row, fetch_trial, to_trial_row

load_dotenv()

app = FastAPI()


@app.exception_handler(APIError)
def handle_postgrest_api_error(request: Request, exc: APIError):
    """Postgrest raises when a query filter can't even be parsed by
    Postgres -- e.g. a truncated/malformed patient_id sent as a uuid
    filter (code 22P02). That's functionally "no such record", not a
    server failure, so surface it as a 404 with a JSON body instead of
    an unhandled 500 with a plain-text body (which breaks every
    frontend `await res.json()` call and shows up as a generic
    "could not reach the server" error)."""
    if exc.code == "22P02":
        return JSONResponse(status_code=404, content={"detail": "Patient not found"})
    return JSONResponse(status_code=502, content={"detail": exc.message or "Database error"})


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/trials/{nct_id}")
async def get_trial(nct_id: str):
    """Fetches live trial metadata from ClinicalTrials.gov, exactly as
    before, for any real NCT ID. Additive fallback: only when that lookup
    404s do we check our own `trials` table -- this lets a locally-defined
    trial (no ClinicalTrials.gov record) still be loaded through the same
    dashboard/API surface as real trials, without changing behavior for
    any trial that IS on ClinicalTrials.gov."""
    try:
        return await fetch_trial(nct_id)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        client = get_client()
        res = client.table("trials").select("*").eq("nct_id", nct_id).limit(1).execute()
        if not res.data:
            raise
        row = res.data[0]
        return {
            "nct_id": row["nct_id"],
            "title": row.get("title"),
            "phase": row.get("phase"),
            "overall_status": row.get("status"),
            "primary_endpoint": row.get("primary_endpoint"),
            "eligibility_criteria": None,
            # A locally-uploaded trial (no ClinicalTrials.gov record) has no
            # source to read these from -- same shape as the CT.gov path,
            # just empty, so the frontend doesn't need to special-case it.
            "brief_summary": None,
            "conditions": [],
            "study_type": None,
            "interventions": [],
            "minimum_age": None,
            "maximum_age": None,
            "eligible_sex": None,
            "healthy_volunteers": None,
        }


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

    rows = [criterion_to_db_row(nct_id, c) for c in criteria]
    inserted = client.table("trial_criteria").insert(rows).execute()

    return ParseCriteriaToDBResponse(
        nct_id=nct_id,
        total=len(criteria),
        inclusion=sum(1 for c in criteria if c.type == "inclusion"),
        exclusion=sum(1 for c in criteria if c.type == "exclusion"),
        needs_review=sum(1 for c in criteria if c.needs_review),
        criteria=[TrialCriterionRow(**row) for row in inserted.data],
    )


@app.post("/trials/upload-document", response_model=UploadTrialResponse)
async def upload_trial_document(file: UploadFile = File(...)):
    """Researcher-portal trial creation from an uploaded protocol/summary
    document (PDF, or a photo/scan) instead of a ClinicalTrials.gov NCT id
    -- for a trial that has no ClinicalTrials.gov record (a house study, a
    draft protocol). Gemini reads the title/phase/primary endpoint and the
    eligibility criteria section (llm.extract_trial_document /
    extract_trial_document_from_file), and that text is handed to the
    EXISTING, unchanged parse_criteria -- same function the NCT-id flow
    above calls. No new parsing/matching logic; just a new way to reach it."""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(content) > _MAX_UPLOAD_FILE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File too large (max {_MAX_UPLOAD_FILE_BYTES // (1024 * 1024)}MB)",
        )

    client = get_client()
    mime_type = file.content_type or ""
    if mime_type == "text/plain":
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="Could not read this file as UTF-8 text")
        if not text.strip():
            raise HTTPException(status_code=400, detail="Uploaded file is empty")
        result = ingest_trial_document(client, text)
    elif mime_type in SUPPORTED_DOCUMENT_FILE_MIME_TYPES:
        result = ingest_trial_document_file(client, content, mime_type)
    else:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type: {mime_type or 'unknown'}. "
            "Upload a PDF, PNG, JPEG, or plain text file.",
        )

    if result.get("error"):
        raise HTTPException(status_code=422, detail=result["error"])

    return UploadTrialResponse(**{k: v for k, v in result.items() if k != "error"})


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


@app.get("/lab-results/{lab_result_id}", response_model=LabResultRecord)
def get_lab_result(lab_result_id: int):
    """Source data verification: fetches the exact lab_results row a
    match verdict's source_lab_result_id cites, so a reviewer can confirm
    the value/reference range/test date behind an automated decision --
    not just trust an ID. Read-only, no schema change."""
    client = get_client()
    res = client.table("lab_results").select("*").eq("lab_result_id", lab_result_id).limit(1).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail=f"Lab result {lab_result_id} not found")
    return res.data[0]


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

    demographics = {}
    if to_evaluate:
        demo_rows = (
            client.table("patients")
            .select("patient_id, age, gender")
            .in_("patient_id", to_evaluate)
            .execute()
            .data
        )
        demographics = {r["patient_id"]: (r["age"], r["gender"]) for r in demo_rows}

    candidates = []
    for pid in to_evaluate:
        overall, results = match_patient_db(client, pid, nct_id, criteria_rows=criteria_rows)
        age, sex = demographics.get(pid, (None, None))
        candidates.append(
            DBCandidateSummary(
                patient_id=pid,
                age=age,
                sex=sex,
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


@app.get("/trials/{nct_id}/progress", response_model=TrialProgressResponse)
def get_trial_progress(nct_id: str, primary_test_code: str | None = None):
    client = get_client()
    progress = compute_trial_progress(client, nct_id, primary_test_code=primary_test_code)
    return TrialProgressResponse(**progress)


@app.post("/trials/{nct_id}/compute-metrics", response_model=TrialProgressResponse)
def compute_metrics(nct_id: str, primary_test_code: str | None = None):
    client = get_client()
    progress = compute_trial_progress(client, nct_id, primary_test_code=primary_test_code)
    store_trial_metrics(client, progress)
    return TrialProgressResponse(**progress)


def _run_transition(fn, *args):
    try:
        return fn(*args)
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/trials/{nct_id}/patients/{patient_id}/invite", response_model=EnrollmentRecord)
def invite(nct_id: str, patient_id: str):
    client = get_client()
    return _run_transition(invite_patient, client, patient_id, nct_id)


@app.post("/trials/{nct_id}/patients/{patient_id}/consent", response_model=EnrollmentRecord)
def consent(nct_id: str, patient_id: str):
    client = get_client()
    return _run_transition(consent_patient, client, patient_id, nct_id)


@app.post("/trials/{nct_id}/patients/{patient_id}/enroll", response_model=EnrollmentRecord)
def enroll(nct_id: str, patient_id: str):
    client = get_client()
    return _run_transition(enroll_patient, client, patient_id, nct_id)


@app.post("/trials/{nct_id}/patients/{patient_id}/withdraw", response_model=EnrollmentRecord)
def withdraw(nct_id: str, patient_id: str):
    client = get_client()
    return _run_transition(withdraw_patient, client, patient_id, nct_id)


@app.post("/trials/{nct_id}/patients/{patient_id}/decline", response_model=EnrollmentRecord)
def decline(nct_id: str, patient_id: str):
    client = get_client()
    return _run_transition(decline_patient, client, patient_id, nct_id)


@app.get("/trials/{nct_id}/enrollment", response_model=list[EnrollmentRecord])
def get_enrollment(nct_id: str):
    client = get_client()
    return list_enrollment(client, nct_id)


@app.get("/patients/{patient_id}/enrollment", response_model=list[EnrollmentRecord])
def get_patient_enrollment(patient_id: str):
    """Every invitation/enrollment record for one patient, across all
    trials -- the reverse of GET /trials/{nct_id}/enrollment. Read-only,
    same patient_trial table. Powers the patient portal home."""
    client = get_client()
    return list_patient_enrollment(client, patient_id)


@app.get("/trials/{nct_id}/audit", response_model=list[AuditLogRow])
def get_trial_audit(nct_id: str, limit: int = 200):
    client = get_client()
    return list_trial_audit(client, nct_id, limit=limit)


@app.post("/patients/signup", response_model=PatientSignupResponse)
def patient_signup(request: PatientSignupRequest):
    """Patient-portal self-signup: creates a new `patients` row from the
    essential intake details on the signup form and returns the new
    patient_id, so the frontend can sign the patient straight in."""
    client = get_client()
    result = create_patient(client, request)
    return PatientSignupResponse(**result)


@app.get("/patients", response_model=list[Patient])
def list_patients():
    return load_patients()


@app.get("/patients/{patient_id}", response_model=Patient)
def get_patient_by_id(patient_id: str):
    patient = get_patient(patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail=f"Patient {patient_id} not found")
    return patient


@app.post("/patients/{patient_id}/upload-lab", response_model=UploadLabResponse)
def upload_lab_report(patient_id: str, request: UploadLabRequest):
    """Patient-portal lab report upload -> AI extraction (step 9, source
    data verification): pasted free-text goes to the same Gemini client the
    criteria parser uses, gets extracted into structured lab values, and
    each one is written into the existing lab_results table citing the
    patient_documents row created for this upload. No schema change."""
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="text must not be empty")

    client = get_client()
    patient_check = (
        client.table("patients").select("patient_id").eq("patient_id", patient_id).limit(1).execute()
    )
    if not patient_check.data:
        raise HTTPException(status_code=404, detail=f"Patient {patient_id} not found")

    result = ingest_lab_report(client, patient_id, request.text)
    return UploadLabResponse(patient_id=patient_id, **result)


_MAX_UPLOAD_FILE_BYTES = 15 * 1024 * 1024  # 15MB -- comfortably under Gemini's inline-data limit


@app.post("/patients/{patient_id}/upload-lab-file", response_model=UploadLabResponse)
async def upload_lab_report_file(patient_id: str, file: UploadFile = File(...)):
    """Same pipeline as /upload-lab, but for an uploaded file instead of
    pasted text -- a PDF, or a photo/scan of a printed report. A plain-text
    file is decoded and routed through the exact same text path as
    /upload-lab; a PDF/PNG/JPEG is sent to Gemini as an inline document/
    image part (llm.extract_lab_values_from_file) -- same Gemini client/
    model, no local PDF/OCR library. No schema change."""
    client = get_client()
    patient_check = (
        client.table("patients").select("patient_id").eq("patient_id", patient_id).limit(1).execute()
    )
    if not patient_check.data:
        raise HTTPException(status_code=404, detail=f"Patient {patient_id} not found")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(content) > _MAX_UPLOAD_FILE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File too large (max {_MAX_UPLOAD_FILE_BYTES // (1024 * 1024)}MB)",
        )

    mime_type = file.content_type or ""
    if mime_type == "text/plain":
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="Could not read this file as UTF-8 text")
        if not text.strip():
            raise HTTPException(status_code=400, detail="Uploaded file is empty")
        result = ingest_lab_report(client, patient_id, text)
    elif mime_type in SUPPORTED_DOCUMENT_FILE_MIME_TYPES:
        result = ingest_lab_report_file(client, patient_id, content, mime_type, file.filename)
    else:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type: {mime_type or 'unknown'}. "
            "Upload a PDF, PNG, JPEG, or plain text file.",
        )

    return UploadLabResponse(patient_id=patient_id, **result)


@app.post("/patients/{patient_id}/upload-document", response_model=UploadDocumentResponse)
async def upload_patient_document(
    patient_id: str, document_type: str = Form(...), file: UploadFile = File(...)
):
    """Patient-portal "supporting documents" upload -- referral letters,
    imaging reports, medical reports, consent forms, or anything else the
    patient wants on file, alongside the dedicated lab-report upload above.
    Purely a filing action: no LLM extraction, just one patient_documents
    row (document_upload.store_patient_document) so it's visible on the
    patient's record. No schema change."""
    if document_type not in SUPPORTED_DOCUMENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported document_type: {document_type!r}. "
            f"Must be one of: {', '.join(sorted(SUPPORTED_DOCUMENT_TYPES))}.",
        )

    client = get_client()
    patient_check = (
        client.table("patients").select("patient_id").eq("patient_id", patient_id).limit(1).execute()
    )
    if not patient_check.data:
        raise HTTPException(status_code=404, detail=f"Patient {patient_id} not found")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(content) > _MAX_UPLOAD_FILE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File too large (max {_MAX_UPLOAD_FILE_BYTES // (1024 * 1024)}MB)",
        )

    result = store_patient_document(client, patient_id, document_type, content, file.filename)
    return UploadDocumentResponse(patient_id=patient_id, **result)


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
