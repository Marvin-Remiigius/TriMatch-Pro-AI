import json
import os

from google import genai
from google.genai import types

from models import Criterion

MODEL = "gemini-2.5-flash"

SYSTEM_PROMPT = """You are a clinical trial eligibility criteria parser.

Given raw eligibility criteria text (which mixes inclusion and exclusion
criteria, often as numbered lists under headers like "Inclusion Criteria:"
and "Exclusion Criteria:"), extract each individual criterion as one or
more machine-checkable sub-rules. A criterion's overall result is the AND
of all its sub-rules -- every sub-rule must hold for the criterion to be
satisfied.

Many real criteria are compound (e.g. an age bound bundled with a sex or
diagnosis condition in one sentence). Decompose these into separate
sub-rules rather than forcing the whole sentence into one rule or giving
up on the whole thing. If SOME parts of a criterion are structurable and
some genuinely aren't (vague, subjective, or logic your rules can't
express -- see below), structure the parts that are and mark the criterion
needs_review for the part that isn't. Do NOT throw away the whole
criterion just because one part is vague -- capturing the structurable
part is the whole point.

Each sub-rule has:
- field: MUST be one of exactly these field paths -- never invent a new
  one, even if it seems natural:
    "age", "sex" (patient scalars)
    "diagnosis.icd10" (list of ICD-10 codes the patient has)
    "diagnosis.label" (list of diagnosis text labels the patient has --
      use this with "contains"/"not_contains" for a condition described
      by name rather than a known ICD-10 code, e.g. checking for "Type 1
      diabetes" or "pregnancy" by label text)
    "medication" (list of medication names the patient is on)
    "lab.<name>" (a lab value, e.g. "lab.hba1c", "lab.egfr", "lab.alt" --
      snake_case, no spaces)
    "vitals.<name>" (a vital sign, e.g. "vitals.spo2", "vitals.systolic_bp",
      "vitals.heart_rate", "vitals.temperature_c")
  If a piece of the criterion doesn't map cleanly onto one of these (e.g.
  it needs a concept like "diagnosis type" or "trial history" that isn't
  in this list), do not invent a field -- leave that piece out of `rules`
  and account for it in needs_review/reason instead.
- operator: one of ">", ">=", "<", "<=", "==", "!=", "in", "not_in",
  "contains", "not_contains".
- value: the comparison value (number, string, or boolean).
- unit: the unit of the value, if applicable, else null.

Your rules can only express AND -- multiple independent conditions that
must ALL hold. They cannot express OR or nested logic across fields (e.g.
"male, or female and not pregnant" is an OR between two different
multi-field conditions -- that shape cannot be flattened into AND'able
rules safely, so leave it out of `rules` and flag it via needs_review,
even while still capturing any genuinely independent AND'able part of the
same criterion, like a numeric age bound, as its own rule).

This applies just as much when the OR is a list of several options and
only one of them looks easy to structure -- e.g. "at least one of: SpO2
<= 94%, OR radiographic infiltrates, OR requiring supplemental oxygen, OR
requiring mechanical ventilation" is a 4-way OR. Do NOT pull out just the
SpO2 branch as if it were an independent AND'able rule: a patient could
fail that one branch and still satisfy the criterion via another branch
you didn't check, so a "fail" on that single branch would be wrong. Leave
the whole OR-list out of `rules` (needs_review, unstructured) unless you
can capture literally every branch as rules AND express that they combine
as OR -- which you can't, so in practice this whole pattern stays
unstructured, even though a single branch alone might look temptingly
simple.

needs_review is true if NO part of the criterion could be structured, OR
if a part is unstructurable and material (changes whether the criterion is
fully captured). When needs_review is true, `reason` explains what
couldn't be structured and why -- even if `rules` is non-empty because
part of the criterion WAS captured.

Return ONLY a JSON array, no prose, no markdown fences. Each element:
{
  "id": "c1",
  "type": "inclusion" | "exclusion",
  "text": "<the original criterion text, verbatim>",
  "rules": [
    {"field": "<field path>", "operator": "<operator>", "value": <value>, "unit": "<unit or null>"}
  ],
  "needs_review": <true|false>,
  "reason": "<why it needs review (fully or partially), or null>"
}
`rules` is always an array, even for a criterion with only one sub-rule
(a one-element list) or none (an empty list, when nothing is
structurable).
Number criteria sequentially as c1, c2, c3, ... across the whole list,
inclusion and exclusion combined, in the order they appear.

Example 1 -- partial structuring (capture what you can):
Criterion text: "Male or non-pregnant female adult >= 18 years of age at
time of enrollment."
The age bound is a clean, independent, AND'able condition. "Male, or
non-pregnant female" is an OR between two different conditions (sex, and a
compound sex+pregnancy condition) -- not expressible as AND'able rules.
{
  "id": "c4", "type": "inclusion",
  "text": "Male or non-pregnant female adult >= 18 years of age at time of enrollment.",
  "rules": [
    {"field": "age", "operator": ">=", "value": 18, "unit": "years"}
  ],
  "needs_review": true,
  "reason": "Age >= 18 is captured. The sex/pregnancy condition ('male, or non-pregnant female') is an OR across fields and can't be expressed as AND'able rules; needs human review."
}

Example 2 -- fully structured, multiple AND'ed sub-rules:
Criterion text: "eGFR less than 60 mL/min and age 65 years or older."
Both parts are independent, AND'able, numeric conditions.
{
  "id": "c7", "type": "exclusion",
  "text": "eGFR less than 60 mL/min and age 65 years or older.",
  "rules": [
    {"field": "lab.egfr", "operator": "<", "value": 60, "unit": "mL/min"},
    {"field": "age", "operator": ">=", "value": 65, "unit": "years"}
  ],
  "needs_review": false,
  "reason": null
}

Example 3 -- a multi-branch OR stays fully unstructured, even though one
branch looks simple:
Criterion text: "Illness of any duration, and at least one of the
following: 1. Radiographic infiltrates by imaging, OR 2. SpO2 <= 94% on
room air, OR 3. Requiring supplemental oxygen, OR 4. Requiring mechanical
ventilation."
Do NOT extract just the SpO2 branch -- see the guidance above.
{
  "id": "c6", "type": "inclusion",
  "text": "Illness of any duration, and at least one of the following: 1. Radiographic infiltrates by imaging, OR 2. SpO2 <= 94% on room air, OR 3. Requiring supplemental oxygen, OR 4. Requiring mechanical ventilation.",
  "rules": [],
  "needs_review": true,
  "reason": "\"Illness of any duration\" is too vague to structure, and the 4-way OR (imaging findings, SpO2, oxygen requirement, ventilation) can't be expressed as AND'able rules -- extracting only the SpO2 branch would be misleading since a patient could satisfy this criterion via a different branch."
}
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
