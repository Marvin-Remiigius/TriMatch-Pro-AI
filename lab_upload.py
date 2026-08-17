"""
Patient-portal lab report upload -> AI extraction -> existing normalized
store. Flowchart step 9 (source data verification): a patient pastes a
free-text lab report, the LLM (llm.extract_lab_values, the same Gemini
client the criteria parser uses) extracts structured values, and each one
is written into the EXISTING `lab_results` table with a citation back to
the EXISTING `patient_documents` row for the upload. No schema change --
`lab_report_id` (a free-text column, not a foreign key in the existing
schema) carries "DOC<document_id>" so every inserted lab_results row is
traceable to its source document without altering either table.
"""

import hashlib
import re
from datetime import datetime, timezone

from llm import extract_lab_values

# The test_code convention already used by the seeded lab_results data
# (scripts/seed_extra_labs.py and the original dataset) -- extracted values
# are normalized onto these so they're matchable/comparable with existing
# readings, exactly as the brief requires.
CANONICAL_TESTS = {
    "HBA1C": {"test_name": "HbA1c", "unit": "%"},
    "EGFR": {"test_name": "eGFR", "unit": "mL/min/1.73m2"},
    "ALT": {"test_name": "Alanine Aminotransferase (ALT)", "unit": "U/L"},
    "AST": {"test_name": "Aspartate Aminotransferase (AST)", "unit": "U/L"},
    "CHOL": {"test_name": "Cholesterol (Total)", "unit": "mg/dL"},
    "GLU": {"test_name": "Glucose", "unit": "mg/dL"},
    "CREAT": {"test_name": "Creatinine", "unit": "mg/dL"},
    "HGB": {"test_name": "Hemoglobin", "unit": "g/dL"},
    "WBC": {"test_name": "White Blood Cell Count", "unit": "x10^3/uL"},
}

# Longest-key-first substring match, so "hemoglobin a1c" resolves to HBA1C
# rather than the shorter "hemoglobin" -> HGB match.
_SYNONYMS = {
    "glycated hemoglobin": "HBA1C",
    "glycated haemoglobin": "HBA1C",
    "hemoglobin a1c": "HBA1C",
    "haemoglobin a1c": "HBA1C",
    "hba1c": "HBA1C",
    "a1c": "HBA1C",
    "estimated glomerular filtration rate": "EGFR",
    "glomerular filtration rate": "EGFR",
    "estimated gfr": "EGFR",
    "egfr": "EGFR",
    "alanine aminotransferase": "ALT",
    "sgpt": "ALT",
    "alt": "ALT",
    "aspartate aminotransferase": "AST",
    "sgot": "AST",
    "ast": "AST",
    "total cholesterol": "CHOL",
    "cholesterol": "CHOL",
    "fasting glucose": "GLU",
    "blood glucose": "GLU",
    "glucose": "GLU",
    "serum creatinine": "CREAT",
    "creatinine": "CREAT",
    "white blood cell count": "WBC",
    "white blood cell": "WBC",
    "leukocyte count": "WBC",
    "wbc": "WBC",
    "hemoglobin": "HGB",
    "haemoglobin": "HGB",
}
_SYNONYM_KEYS_BY_LENGTH = sorted(_SYNONYMS, key=len, reverse=True)


def _normalize_test_code(test_name: str, llm_code: str | None) -> tuple[str, bool]:
    """Returns (test_code, flagged). flagged=True means this couldn't be
    confidently mapped to our existing test_code convention -- per the
    brief, it's still stored (never dropped), just flagged."""
    if llm_code:
        upper = llm_code.strip().upper()
        if upper in CANONICAL_TESTS:
            return upper, False

    lowered = test_name.lower()
    for key in _SYNONYM_KEYS_BY_LENGTH:
        if key in lowered:
            return _SYNONYMS[key], False

    fallback = re.sub(r"[^A-Za-z0-9]+", "_", test_name.strip()).strip("_").upper()
    return (fallback[:40] or "UNKNOWN"), True


def ingest_lab_report(client, patient_id: str, raw_text: str) -> dict:
    """Extracts structured lab values from raw_text via Gemini, writes one
    patient_documents row for the upload, then inserts the extracted values
    into lab_results referencing it. Caller is responsible for confirming
    patient_id exists first."""
    extraction = extract_lab_values(raw_text)
    items = extraction["items"]

    now_iso = datetime.now(timezone.utc).isoformat()
    report_date = next((i["test_date"] for i in items if i["test_date"]), None) or now_iso
    document_name = f"Lab report uploaded {now_iso[:10]}"

    document = (
        client.table("patient_documents")
        .insert(
            {
                "patient_id": patient_id,
                "document_type": "lab_report",
                "document_name": document_name,
                "document_date": report_date,
                # No real file storage in this demo -- the raw pasted text
                # itself is the source record, kept inline so it stays
                # traceable (same spirit as file_path, just no filesystem).
                "file_path": raw_text,
                "document_hash": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
            }
        )
        .execute()
        .data[0]
    )
    document_id = document["document_id"]
    lab_report_id = f"DOC{document_id}"

    normalized = []
    for item in items:
        test_code, flagged = _normalize_test_code(item["test_name"], item["test_code"])
        canonical = CANONICAL_TESTS.get(test_code)
        normalized.append(
            {
                "row": {
                    "patient_id": patient_id,
                    "test_name": canonical["test_name"] if canonical else item["test_name"],
                    "test_code": test_code,
                    "value": item["value"],
                    "unit": item["unit"] or (canonical["unit"] if canonical else None),
                    "test_date": item["test_date"] or report_date,
                    "lab_report_id": lab_report_id,
                },
                "flagged": flagged,
            }
        )

    inserted_rows = []
    if normalized:
        inserted_rows = (
            client.table("lab_results").insert([n["row"] for n in normalized]).execute().data
        )

    extracted = [
        {
            "lab_result_id": row["lab_result_id"],
            "test_code": row["test_code"],
            "test_name": row["test_name"],
            "value": row["value"],
            "unit": row["unit"],
            "test_date": row["test_date"],
            "flagged_unmapped": n["flagged"],
        }
        for row, n in zip(inserted_rows, normalized)
    ]

    client.table("audit_log").insert(
        {
            "actor": "patient",
            "action": "lab_report.uploaded",
            "entity": "patient_documents",
            "entity_id": str(document_id),
            "source_ref": lab_report_id,
            "detail": {
                "patient_id": patient_id,
                "extracted_count": len(extracted),
                "dropped_count": extraction["dropped_count"],
                "test_codes": [e["test_code"] for e in extracted],
                "llm_error": extraction["error"],
            },
        }
    ).execute()

    return {
        "document_id": document_id,
        "document_name": document_name,
        "document_date": report_date,
        "extracted": extracted,
        "dropped_count": extraction["dropped_count"],
        "llm_error": extraction["error"],
    }
