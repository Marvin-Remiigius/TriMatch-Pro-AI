"""Patient-portal self-signup: creates a new row in the existing `patients`
table from the essential intake details collected on the signup form.
Clinical data (diagnoses, labs, medications) isn't collected here -- it
comes in later via the existing lab/document upload flows, same as it does
for any patient already in the database."""

import uuid
from datetime import date, datetime, timezone

from models import PatientSignupRequest


def _compute_age(dob: date) -> int:
    today = datetime.now(timezone.utc).date()
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return max(age, 0)


def create_patient(client, request: PatientSignupRequest) -> dict:
    patient_id = str(uuid.uuid4())
    name = f"{request.first_name} {request.last_name}".strip()
    age = _compute_age(request.date_of_birth)

    row = {
        "patient_id": patient_id,
        "name": name,
        "first_name": request.first_name,
        "last_name": request.last_name,
        "date_of_birth": request.date_of_birth.isoformat(),
        "age": age,
        "gender": request.gender,
        "email": request.email,
        "phone": request.phone,
        "city": request.city,
        "country": request.country,
        "blood_group": request.blood_group,
        "ethnicity": request.ethnicity,
    }

    client.table("patients").insert(row).execute()

    return {"patient_id": patient_id, "name": name, "age": age, "gender": request.gender}
