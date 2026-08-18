import json

import httpx
from fastapi import HTTPException

from models import Criterion

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
    outcomes = protocol.get("outcomesModule", {})
    description = protocol.get("descriptionModule", {})
    conditions_module = protocol.get("conditionsModule", {})
    arms_module = protocol.get("armsInterventionsModule", {})

    primary_outcomes = outcomes.get("primaryOutcomes", [])
    primary_endpoint = primary_outcomes[0]["measure"] if primary_outcomes else None

    interventions = [
        {"type": i.get("type"), "name": i.get("name")}
        for i in arms_module.get("interventions", [])
    ]

    return {
        "nct_id": identification.get("nctId", nct_id),
        "title": identification.get("briefTitle"),
        "phase": design.get("phases", []),
        "overall_status": status.get("overallStatus"),
        "eligibility_criteria": eligibility.get("eligibilityCriteria"),
        "primary_endpoint": primary_endpoint,
        # Additive context fields -- surfaced on the patient-facing invitation
        # page so patients see more than just a title and an ID. None of
        # this is used by matching/parsing; purely for display.
        "brief_summary": description.get("briefSummary"),
        "conditions": conditions_module.get("conditions", []),
        "study_type": design.get("studyType"),
        "interventions": interventions,
        "minimum_age": eligibility.get("minimumAge"),
        "maximum_age": eligibility.get("maximumAge"),
        "eligible_sex": eligibility.get("sex"),
        "healthy_volunteers": eligibility.get("healthyVolunteers"),
    }


def to_trial_row(trial: dict) -> dict:
    """Shape a fetch_trial() dict into a trials-table row (nct_id/title/
    phase/status/primary_endpoint)."""
    phases = trial.get("phase") or []
    return {
        "nct_id": trial["nct_id"],
        "title": trial.get("title"),
        "phase": ", ".join(phases) if phases else None,
        "status": trial.get("overall_status"),
        "primary_endpoint": trial.get("primary_endpoint"),
    }


def criterion_to_db_row(nct_id: str, c: Criterion) -> dict:
    """Shapes a parsed Criterion into a trial_criteria row. No DB schema
    change: a criterion with 0-1 rules is encoded exactly as before
    (single scalar in `value`); a criterion with 2+ rules stores the full
    rule list as JSON in that same `value` column (field/operator/unit
    mirror the first rule, for backward compat with anything -- e.g.
    coarse_filter.py -- that only reads the top-level columns). A criterion
    with rule_groups (OR of AND-groups, for alternative pathways) stores
    {"rule_groups": [[...], ...]} as JSON in the same `value` column -- a
    JSON *object*, distinguishable from the flat rule-list *array* encoding
    above, so db_matching.py can tell them apart without a schema change."""
    if c.rule_groups:
        first_group = c.rule_groups[0] if c.rule_groups else []
        first = first_group[0] if first_group else None
        return {
            "nct_id": nct_id,
            "type": c.type,
            "raw_text": c.text,
            "field": first.field if first else None,
            "operator": first.operator if first else None,
            "value": json.dumps(
                {"rule_groups": [[r.model_dump() for r in group] for group in c.rule_groups]}
            ),
            "unit": first.unit if first else None,
            "needs_review": c.needs_review,
        }

    if c.rules and len(c.rules) > 1:
        first = c.rules[0]
        return {
            "nct_id": nct_id,
            "type": c.type,
            "raw_text": c.text,
            "field": first.field,
            "operator": first.operator,
            "value": json.dumps([r.model_dump() for r in c.rules]),
            "unit": first.unit,
            "needs_review": c.needs_review,
        }

    if c.rules and len(c.rules) == 1:
        r = c.rules[0]
        return {
            "nct_id": nct_id,
            "type": c.type,
            "raw_text": c.text,
            "field": r.field,
            "operator": r.operator,
            "value": json.dumps(r.value) if r.value is not None else None,
            "unit": r.unit,
            "needs_review": c.needs_review,
        }

    return {
        "nct_id": nct_id,
        "type": c.type,
        "raw_text": c.text,
        "field": c.field,
        "operator": c.operator,
        "value": json.dumps(c.value) if c.value is not None else None,
        "unit": c.unit,
        "needs_review": c.needs_review,
    }
