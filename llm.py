import json
import os

from google import genai
from google.genai import types

from models import Criterion

MODEL = "gemini-2.5-flash"

SYSTEM_PROMPT = """You are a clinical trial eligibility criteria parser.

Given raw eligibility criteria text (which mixes inclusion and exclusion
criteria, often as numbered lists under headers like "Inclusion Criteria:"
and "Exclusion Criteria:"), extract each individual criterion as a
structured rule.

For each criterion, try to express it as a machine-checkable rule with:
- field: a dotted path into patient data, e.g. "age", "sex",
  "diagnosis.icd10", "lab.hba1c", "lab.egfr", "vitals.spo2",
  "medication" (use snake_case lab names, no spaces).
- operator: one of ">", ">=", "<", "<=", "==", "!=", "in", "not_in",
  "contains", "not_contains".
- value: the comparison value (number, string, or boolean).
- unit: the unit of the value, if applicable, else null.

If a criterion is too vague, subjective, procedural, or compound to reduce
to a single field/operator/value rule (e.g. "willing to comply with study
procedures", "informed consent obtained"), do NOT guess. Instead set
needs_review to true and leave field/operator/value/unit null, and put a
short reason explaining why it can't be structured.

Return ONLY a JSON array, no prose, no markdown fences. Each element:
{
  "id": "c1",
  "type": "inclusion" | "exclusion",
  "text": "<the original criterion text, verbatim>",
  "field": "<field path or null>",
  "operator": "<operator or null>",
  "value": <value or null>,
  "unit": "<unit or null>",
  "needs_review": <true|false>,
  "reason": "<why it needs review, or null>"
}
Number criteria sequentially as c1, c2, c3, ... across the whole list,
inclusion and exclusion combined, in the order they appear.
"""


def _fallback_criterion(raw_text: str, reason: str) -> Criterion:
    return Criterion(
        id="c1",
        type="inclusion",
        text=raw_text.strip()[:500],
        needs_review=True,
        reason=reason,
    )


def parse_criteria(raw_text: str) -> list[Criterion]:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return [_fallback_criterion(raw_text, "GEMINI_API_KEY is not configured")]

    client = genai.Client(api_key=api_key)

    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=raw_text,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0,
            ),
        )
    except Exception as exc:
        return [_fallback_criterion(raw_text, f"LLM call failed: {exc}")]

    try:
        parsed = json.loads(response.text)
    except (json.JSONDecodeError, TypeError) as exc:
        return [_fallback_criterion(raw_text, f"LLM returned malformed JSON: {exc}")]

    if not isinstance(parsed, list):
        return [_fallback_criterion(raw_text, "LLM response was not a JSON array")]

    criteria = []
    for i, item in enumerate(parsed, start=1):
        try:
            criteria.append(Criterion(**item))
        except Exception as exc:
            criteria.append(
                Criterion(
                    id=item.get("id", f"c{i}") if isinstance(item, dict) else f"c{i}",
                    type=item.get("type", "inclusion")
                    if isinstance(item, dict) and item.get("type") in ("inclusion", "exclusion")
                    else "inclusion",
                    text=str(item.get("text", ""))[:500] if isinstance(item, dict) else str(item)[:500],
                    needs_review=True,
                    reason=f"Could not validate LLM output for this item: {exc}",
                )
            )

    if not criteria:
        return [_fallback_criterion(raw_text, "LLM returned an empty list")]

    return criteria
