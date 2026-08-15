import httpx
from fastapi import FastAPI, HTTPException

from models import Patient
from patients import get_patient, load_patients

app = FastAPI()

CLINICAL_TRIALS_API = "https://clinicaltrials.gov/api/v2/studies"


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/trials/{nct_id}")
async def get_trial(nct_id: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{CLINICAL_TRIALS_API}/{nct_id}")

    if response.status_code in (400, 404):
        raise HTTPException(status_code=404, detail=f"Trial {nct_id} not found")
    response.raise_for_status()

    data = response.json()
    protocol = data["protocolSection"]
    identification = protocol.get("identificationModule", {})
    status = protocol.get("statusModule", {})
    design = protocol.get("designModule", {})
    eligibility = protocol.get("eligibilityModule", {})

    return {
        "nct_id": identification.get("nctId", nct_id),
        "title": identification.get("briefTitle"),
        "phase": design.get("phases", []),
        "overall_status": status.get("overallStatus"),
        "eligibility_criteria": eligibility.get("eligibilityCriteria"),
    }


@app.get("/patients", response_model=list[Patient])
def list_patients():
    return load_patients()


@app.get("/patients/{patient_id}", response_model=Patient)
def get_patient_by_id(patient_id: str):
    patient = get_patient(patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail=f"Patient {patient_id} not found")
    return patient
