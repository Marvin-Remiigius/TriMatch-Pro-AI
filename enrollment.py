"""
patient_trial state machine + its audit trail.

Valid transitions only:
  invite:  (no record)          -> invited
  consent: invited              -> accepted -> consented   (two audit rows;
                                                              enforces terms
                                                              were shown while
                                                              status was still
                                                              'invited')
  enroll:  consented            -> enrolled   (sets baseline_date -- this is
                                                the anchor Step 6's
                                                baseline-vs-latest comparison
                                                depends on)
  decline: invited/accepted     -> declined   (terminal)
  withdraw: invited/accepted/consented/enrolled -> withdrawn (terminal)

Every transition appends one (or two, for consent) row to audit_log.
audit_log is never updated or deleted -- each call is a plain insert.
"""

from datetime import datetime, timezone


class InvalidTransitionError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_audit(client, actor: str, action: str, patient_id: str, nct_id: str, detail: dict) -> None:
    client.table("audit_log").insert(
        {
            "actor": actor,
            "action": action,
            "entity": "patient_trial",
            "entity_id": f"{patient_id}:{nct_id}",
            "detail": detail,
        }
    ).execute()


def _get_enrollment(client, patient_id: str, nct_id: str):
    res = (
        client.table("patient_trial")
        .select("*")
        .eq("patient_id", patient_id)
        .eq("nct_id", nct_id)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def invite_patient(client, patient_id: str, nct_id: str) -> dict:
    existing = _get_enrollment(client, patient_id, nct_id)
    if existing is not None:
        raise InvalidTransitionError(
            f"Patient already has an enrollment record for this trial "
            f"(status='{existing['status']}'); cannot re-invite."
        )

    now = _now()
    client.table("patient_trial").insert(
        {"patient_id": patient_id, "nct_id": nct_id, "status": "invited", "invited_at": now}
    ).execute()
    _log_audit(
        client, "researcher", "patient.invited", patient_id, nct_id,
        {"from_status": None, "to_status": "invited", "timestamp": now},
    )
    return _get_enrollment(client, patient_id, nct_id)


def consent_patient(client, patient_id: str, nct_id: str) -> dict:
    existing = _get_enrollment(client, patient_id, nct_id)
    if existing is None or existing["status"] != "invited":
        current = existing["status"] if existing else "not invited"
        raise InvalidTransitionError(
            "Cannot record consent: patient must have been invited and shown "
            f"terms first (status must be 'invited'); current status is '{current}'."
        )

    accepted_at = _now()
    client.table("patient_trial").update(
        {"status": "accepted", "updated_at": accepted_at}
    ).eq("patient_id", patient_id).eq("nct_id", nct_id).execute()
    _log_audit(
        client, "patient", "patient.accepted", patient_id, nct_id,
        {"from_status": "invited", "to_status": "accepted", "timestamp": accepted_at},
    )

    consented_at = _now()
    client.table("patient_trial").update(
        {"status": "consented", "consented_at": consented_at, "updated_at": consented_at}
    ).eq("patient_id", patient_id).eq("nct_id", nct_id).execute()
    _log_audit(
        client, "patient", "consent.recorded", patient_id, nct_id,
        {"from_status": "accepted", "to_status": "consented", "timestamp": consented_at},
    )

    return _get_enrollment(client, patient_id, nct_id)


def enroll_patient(client, patient_id: str, nct_id: str) -> dict:
    existing = _get_enrollment(client, patient_id, nct_id)
    if existing is None or existing["status"] != "consented":
        current = existing["status"] if existing else "not invited"
        raise InvalidTransitionError(
            f"Cannot enroll: patient must be 'consented' first; current status is '{current}'."
        )

    now = _now()
    client.table("patient_trial").update(
        {"status": "enrolled", "enrolled_at": now, "baseline_date": now, "updated_at": now}
    ).eq("patient_id", patient_id).eq("nct_id", nct_id).execute()
    _log_audit(
        client, "researcher", "patient.enrolled", patient_id, nct_id,
        {"from_status": "consented", "to_status": "enrolled", "timestamp": now, "baseline_date": now},
    )
    return _get_enrollment(client, patient_id, nct_id)


def withdraw_patient(client, patient_id: str, nct_id: str) -> dict:
    existing = _get_enrollment(client, patient_id, nct_id)
    active_states = {"invited", "accepted", "consented", "enrolled"}
    if existing is None or existing["status"] not in active_states:
        current = existing["status"] if existing else "not invited"
        raise InvalidTransitionError(
            f"Cannot withdraw: patient is not in an active enrollment state; current status is '{current}'."
        )

    from_status = existing["status"]
    now = _now()
    client.table("patient_trial").update(
        {"status": "withdrawn", "withdrawn_at": now, "updated_at": now}
    ).eq("patient_id", patient_id).eq("nct_id", nct_id).execute()
    _log_audit(
        client, "researcher", "patient.withdrawn", patient_id, nct_id,
        {"from_status": from_status, "to_status": "withdrawn", "timestamp": now},
    )
    return _get_enrollment(client, patient_id, nct_id)


def decline_patient(client, patient_id: str, nct_id: str) -> dict:
    existing = _get_enrollment(client, patient_id, nct_id)
    declinable_states = {"invited", "accepted"}
    if existing is None or existing["status"] not in declinable_states:
        current = existing["status"] if existing else "not invited"
        raise InvalidTransitionError(
            f"Cannot decline: patient must be 'invited' or 'accepted'; current status is '{current}'."
        )

    from_status = existing["status"]
    now = _now()
    client.table("patient_trial").update(
        {"status": "declined", "updated_at": now}
    ).eq("patient_id", patient_id).eq("nct_id", nct_id).execute()
    _log_audit(
        client, "patient", "patient.declined", patient_id, nct_id,
        {"from_status": from_status, "to_status": "declined", "timestamp": now},
    )
    return _get_enrollment(client, patient_id, nct_id)


def list_enrollment(client, nct_id: str) -> list[dict]:
    return client.table("patient_trial").select("*").eq("nct_id", nct_id).execute().data


def list_trial_audit(client, nct_id: str, limit: int = 200) -> list[dict]:
    return (
        client.table("audit_log")
        .select("*")
        .like("entity_id", f"%:{nct_id}")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
        .data
    )
