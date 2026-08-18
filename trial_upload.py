"""
Researcher-portal trial document upload -> new local trial. A researcher
uploads a protocol/summary document (PDF, or a photo/scan) instead of
typing a ClinicalTrials.gov NCT id; Gemini (llm.extract_trial_document /
extract_trial_document_from_file, the same client/model as every other
extraction in this project) reads the title/phase/primary endpoint and the
eligibility criteria section verbatim. The LLM's job stops there -- the
eligibility text is then handed to the EXISTING, unchanged `parse_criteria`
(same function the NCT-id flow uses) to structure it into rules, and
trials.criterion_to_db_row (also unchanged) shapes those into
trial_criteria rows. No schema change, no new matching/parsing logic --
this is purely a new way to get eligibility text into the same pipeline
`/trials/{nct_id}/parse-criteria` already uses for real ClinicalTrials.gov
trials.
"""

import re

from llm import extract_trial_document, extract_trial_document_from_file, parse_criteria
from trials import criterion_to_db_row


def _slugify(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", text.strip()).strip("-").upper()
    return slug[:30] or "TRIAL"


def _generate_local_nct_id(client, title: str | None) -> str:
    """A local trial has no real NCT id, so one is derived from the title
    (or a generic fallback) with the same "TM-..." convention used by the
    existing hand-authored local trials (e.g. TM-METABOLIC-001), suffixed
    to avoid colliding with an existing trials row."""
    base = _slugify(title or "trial")
    existing = {r["nct_id"] for r in client.table("trials").select("nct_id").execute().data}

    n = 1
    candidate = f"TM-{base}-{n:03d}"
    while candidate in existing:
        n += 1
        candidate = f"TM-{base}-{n:03d}"
    return candidate


def ingest_trial_document(client, raw_text: str) -> dict:
    """Pasted/decoded trial document text -> new trial + parsed criteria."""
    doc = extract_trial_document(raw_text)
    return _write_trial(client, doc)


def ingest_trial_document_file(client, file_bytes: bytes, mime_type: str) -> dict:
    """Uploaded trial document file (PDF or photo/scan image) -> new trial
    + parsed criteria. Same pipeline as ingest_trial_document, just reading
    the document/image directly via Gemini instead of decoded text."""
    doc = extract_trial_document_from_file(file_bytes, mime_type)
    return _write_trial(client, doc)


def _write_trial(client, doc: dict) -> dict:
    if doc["error"]:
        return {"error": f"Could not read this document: {doc['error']}"}
    if not doc["eligibility_criteria"]:
        return {
            "error": "No eligibility criteria section could be found in this document. "
            "Make sure the file includes the trial's Inclusion/Exclusion Criteria."
        }

    nct_id = _generate_local_nct_id(client, doc["title"])
    trial_row = {
        "nct_id": nct_id,
        "title": doc["title"] or nct_id,
        "phase": doc["phase"],
        "status": "RECRUITING",
        "primary_endpoint": doc["primary_endpoint"],
    }
    client.table("trials").upsert(trial_row, on_conflict="nct_id").execute()

    criteria = parse_criteria(doc["eligibility_criteria"])

    client.table("trial_criteria").delete().eq("nct_id", nct_id).execute()
    rows = [criterion_to_db_row(nct_id, c) for c in criteria]
    inserted = client.table("trial_criteria").insert(rows).execute()

    client.table("audit_log").insert(
        {
            "actor": "researcher",
            "action": "trial.uploaded",
            "entity": "trials",
            "entity_id": nct_id,
            "detail": {
                "title": trial_row["title"],
                "total_criteria": len(criteria),
                "needs_review": sum(1 for c in criteria if c.needs_review),
            },
        }
    ).execute()

    return {
        "nct_id": nct_id,
        "title": trial_row["title"],
        "phase": trial_row["phase"],
        "primary_endpoint": trial_row["primary_endpoint"],
        "eligibility_criteria": doc["eligibility_criteria"],
        "total": len(criteria),
        "inclusion": sum(1 for c in criteria if c.type == "inclusion"),
        "exclusion": sum(1 for c in criteria if c.type == "exclusion"),
        "needs_review": sum(1 for c in criteria if c.needs_review),
        "criteria": inserted.data,
        "error": None,
    }
