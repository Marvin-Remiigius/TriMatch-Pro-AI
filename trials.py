import httpx
from fastapi import HTTPException

CLINICAL_TRIALS_API = "https://clinicaltrials.gov/api/v2/studies"


async def fetch_trial(nct_id: str) -> dict:
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
