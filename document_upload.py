"""
Patient-portal "supporting documents" upload: referral letters, imaging
reports, medical reports, consent forms, or anything else a patient wants
on file alongside their lab reports. Purely a filing action -- no LLM
extraction, no lab_results rows -- just one row in the EXISTING
patient_documents table (the same table lab_upload.py writes to), so a
researcher reviewing a candidate can see what's on file for them. No
schema change.
"""

import hashlib
from datetime import datetime, timezone

# Deliberately excludes "Lab Results"/"lab_report" -- that has its own
# dedicated extraction flow (lab_upload.py). This covers everything else
# the existing patient_documents.document_type values already anticipate.
SUPPORTED_DOCUMENT_TYPES = {
    "Referral Letter",
    "Imaging Report",
    "Medical Report",
    "Consent Form",
    "Other",
}


def store_patient_document(
    client, patient_id: str, document_type: str, content: bytes, filename: str | None
) -> dict:
    """Files an uploaded document against the patient's record. No content
    is parsed -- just recorded (name, type, hash) so it's traceable, same
    "no real file storage in this demo" approach as lab_upload.py."""
    now_iso = datetime.now(timezone.utc).isoformat()
    document_name = filename or f"{document_type} uploaded {now_iso[:10]}"

    document = (
        client.table("patient_documents")
        .insert(
            {
                "patient_id": patient_id,
                "document_type": document_type,
                "document_name": document_name,
                "document_date": now_iso,
                "file_path": filename or f"uploaded {document_type}",
                "document_hash": hashlib.sha256(content).hexdigest(),
            }
        )
        .execute()
        .data[0]
    )

    client.table("audit_log").insert(
        {
            "actor": "patient",
            "action": "document.uploaded",
            "entity": "patient_documents",
            "entity_id": str(document["document_id"]),
            "detail": {"patient_id": patient_id, "document_type": document_type},
        }
    ).execute()

    return {
        "document_id": document["document_id"],
        "document_name": document["document_name"],
        "document_type": document["document_type"],
        "document_date": document["document_date"],
    }
