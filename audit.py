from datetime import datetime, timezone
from typing import Optional

from models import AuditEntry, CriterionMatch

_LOG: list[AuditEntry] = []


def log_match_results(
    patient_id: str, results: list[CriterionMatch], nct_id: Optional[str] = None
) -> None:
    timestamp = datetime.now(timezone.utc)
    for result in results:
        _LOG.append(
            AuditEntry(
                timestamp=timestamp,
                nct_id=nct_id,
                patient_id=patient_id,
                criterion_id=result.id,
                field=result.field,
                verdict=result.verdict,
                patient_value=result.patient_value,
                reason=result.reason,
            )
        )


def get_audit_log(
    nct_id: Optional[str] = None,
    patient_id: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[AuditEntry]:
    entries = _LOG
    if nct_id is not None:
        entries = [e for e in entries if e.nct_id == nct_id]
    if patient_id is not None:
        entries = [e for e in entries if e.patient_id == patient_id]
    entries = list(reversed(entries))
    if limit is not None:
        entries = entries[:limit]
    return entries


def get_flagged_for_review(
    nct_id: Optional[str] = None,
    patient_id: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[AuditEntry]:
    entries = [e for e in get_audit_log(nct_id, patient_id) if e.verdict == "unknown"]
    if limit is not None:
        entries = entries[:limit]
    return entries
