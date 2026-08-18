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

Some criteria present two or more ALTERNATIVE pathways to satisfy ONE
requirement, where EVERY alternative can independently be fully
structured as its own AND'able rule-set -- e.g. a single sentence with
"and/or" between two clean numeric/field conditions, or a bullet that
ends in a colon (a shared parent requirement) immediately followed by
two or more child bullets that are alternative ways to satisfy it, not
separate independent criteria. When this happens, use `rule_groups`
instead of `rules`: a list of rule-lists, where each inner list is AND'd
internally (same as `rules`) and the outer list is OR'd -- satisfying
ANY ONE group is enough. `rule_groups` and `rules` are mutually
exclusive; populate exactly one (leave the other an empty list or omit
it). If the parent bullet plus its child bullets form one logical
requirement, treat them as ONE criterion with `rule_groups`, not one
criterion per bullet.

Only use `rule_groups` when you can cleanly structure EVERY alternative
-- if even one branch is vague or unstructurable, do not build a
rule_groups entry for the others; leave the whole thing unstructured in
`rules: []` with needs_review instead (same conservative rule as the
4-way OR case above). When `rule_groups` fully captures the criterion
with no leftover unstructurable part, needs_review is false -- it IS
fully expressed, just as OR-of-AND instead of a flat AND. If there's
also a separate, additional qualifier that isn't covered by any
alternative (e.g. a duration/chronicity requirement layered on top),
keep needs_review true and explain the uncovered part in `reason`, even
though `rule_groups` is fully populated for the part that IS captured.

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
  "rule_groups": [
    [{"field": "<field path>", "operator": "<operator>", "value": <value>, "unit": "<unit or null>"}]
  ],
  "needs_review": <true|false>,
  "reason": "<why it needs review (fully or partially), or null>"
}
`rules` is always an array, even for a criterion with only one sub-rule
(a one-element list) or none (an empty list, when nothing is
structurable). `rule_groups` is likewise always an array (of arrays) when
present, but omit it (or use an empty array) unless the criterion is
genuinely an OR of alternative pathways as described above -- most
criteria use `rules`, not `rule_groups`.
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

Example 4 -- alternative pathways, each fully structurable, PLUS a
separate uncaptured qualifier (rule_groups AND needs_review together):
Criterion text: "Evidence of chronic kidney disease consistent with
diabetic kidney disease (DKD), defined by one or more of the following,
with evidence of chronicity (present for >= 3 months): eGFR <60
mL/min/1.73m2, and/or Urine albumin-to-creatinine ratio (UACR) >=30
mg/g."
The eGFR branch and the UACR branch are each a clean, independent,
single-field condition -- express as two one-rule groups in
`rule_groups`. But "with evidence of chronicity (present for >= 3
months)" is a separate temporal-persistence requirement layered on top,
not captured by either branch and not reliably verifiable from a single
lab value -- so needs_review stays true for that reason, even though
rule_groups is fully populated for the eGFR-or-UACR part.
{
  "id": "c9", "type": "inclusion",
  "text": "Evidence of chronic kidney disease consistent with diabetic kidney disease (DKD), defined by one or more of the following, with evidence of chronicity (present for >= 3 months): eGFR <60 mL/min/1.73m2, and/or Urine albumin-to-creatinine ratio (UACR) >=30 mg/g.",
  "rules": [],
  "rule_groups": [
    [{"field": "lab.egfr", "operator": "<", "value": 60, "unit": "mL/min/1.73m2"}],
    [{"field": "lab.uacr", "operator": ">=", "value": 30, "unit": "mg/g"}]
  ],
  "needs_review": true,
  "reason": "eGFR<60 OR UACR>=30 is captured as alternative pathways. The 'evidence of chronicity (present for >= 3 months)' qualifier is a separate temporal requirement not captured by either branch and can't be reliably verified from a single lab value; needs human review."
}

Example 5 -- a parent bullet's requirement met via child bullets, each an
AND of two conditions (rule_groups fully captures it, needs_review false):
Criterion text (a parent bullet ending in ':' followed by two child
bullets that are alternatives):
"Evidence of DKD Stages 1-3:
* Baseline eGFR of 30-60 ml/min/1.73m2 (confirmed 3 months apart with at least one value within 1 year prior to enrollment)
* Individuals with eGFR >=60 ml/min/1.73m2 and albuminuria (UACR >=30mg/g)"
Treat the parent + its two children as ONE criterion, not three. Each
child bullet is itself a clean 2-condition AND (a range, or two
different fields) -- express as two groups, each with 2 rules. Nothing
is left uncaptured, so needs_review is false.
{
  "id": "c3", "type": "inclusion",
  "text": "Evidence of DKD Stages 1-3: Baseline eGFR of 30-60 ml/min/1.73m2 (confirmed 3 months apart with at least one value within 1 year prior to enrollment); or individuals with eGFR >=60 ml/min/1.73m2 and albuminuria (UACR >=30mg/g).",
  "rules": [],
  "rule_groups": [
    [
      {"field": "lab.egfr", "operator": ">=", "value": 30, "unit": "ml/min/1.73m2"},
      {"field": "lab.egfr", "operator": "<=", "value": 60, "unit": "ml/min/1.73m2"}
    ],
    [
      {"field": "lab.egfr", "operator": ">=", "value": 60, "unit": "ml/min/1.73m2"},
      {"field": "lab.uacr", "operator": ">=", "value": 30, "unit": "mg/g"}
    ]
  ],
  "needs_review": false,
  "reason": null
}
"""


LAB_EXTRACTION_SYSTEM_PROMPT = """You are a clinical lab report extractor.

You will be given lab report content -- either as raw text, or as a document
(PDF, or a photo/scan of a printed report) which may mix formatting styles,
abbreviations, units, tables, and handwriting-adjacent print quality. Extract
every individual lab test result you can find as one JSON object per result.

Each element:
{
  "test_name": "<the lab test's name as written in the report>",
  "test_code": "<one of HBA1C, EGFR, ALT, AST, CHOL, GLU, CREAT, HGB, WBC if
    the test clearly matches one of these -- else null>",
  "value": <the numeric result, a number, never a string>,
  "unit": "<the reported unit, or null if none given>",
  "test_date": "<the report/collection date as YYYY-MM-DD if one is present
    in the text, else null>"
}

Map these common synonyms to test_code (case-insensitive, match on meaning
not exact wording):
- Hemoglobin A1c / HbA1c / glycated hemoglobin / glycated haemoglobin / A1c -> HBA1C
- eGFR / estimated GFR / estimated glomerular filtration rate -> EGFR
- ALT / alanine aminotransferase / SGPT -> ALT
- AST / aspartate aminotransferase / SGOT -> AST
- Total Cholesterol / Cholesterol -> CHOL
- Glucose / fasting glucose / blood glucose -> GLU
- Creatinine / serum creatinine -> CREAT
- Hemoglobin (plain, NOT A1c) -> HGB
- WBC / white blood cell count / leukocyte count -> WBC

If a lab result is real but doesn't match any of the codes above, still
include it with "test_code": null and its literal test_name -- do not drop
it just because it's uncoded.

If a page/image is illegible, blank, or not a lab report at all, don't guess
values for it -- just extract whatever genuinely-readable results exist
elsewhere in the document (or return an empty array if none do).

If a value is ambiguous, not actually numeric, or the line doesn't describe
an actual lab test result (e.g. patient demographics, a header, free-text
notes, a doctor's name), SKIP it entirely -- do not guess a value or invent
a result that isn't really there.

Return ONLY a JSON array of these objects, no prose, no markdown fences. If
nothing in the content is a lab result, return an empty array: []
"""


def _strip_markdown_fences(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.split("\n")
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_lab_values_from_contents(contents) -> dict:
    """Shared core: sends `contents` (raw text, or a Part built from
    document/image bytes) to the same Gemini client/model the criteria
    parser uses, with the same call shape (system instruction + strict JSON
    response). Parses defensively -- strips markdown fences, retries once
    on bad JSON, and drops individually malformed entries rather than
    failing the whole extraction. Returns {"items": [...], "dropped_count":
    int, "error": str | None}; items is always a list (possibly empty),
    never raises."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {"items": [], "dropped_count": 0, "error": "GEMINI_API_KEY is not configured"}

    client = genai.Client(api_key=api_key)

    parsed = None
    last_error = None
    for _ in range(2):  # one retry on malformed JSON, per the brief
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=LAB_EXTRACTION_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    temperature=0,
                ),
            )
        except Exception as exc:
            last_error = f"LLM call failed: {exc}"
            continue

        try:
            parsed = json.loads(_strip_markdown_fences(response.text))
            last_error = None
            break
        except (json.JSONDecodeError, TypeError) as exc:
            last_error = f"LLM returned malformed JSON: {exc}"
            parsed = None
            continue

    if parsed is None:
        return {"items": [], "dropped_count": 0, "error": last_error}

    if not isinstance(parsed, list):
        return {"items": [], "dropped_count": 0, "error": "LLM response was not a JSON array"}

    items = []
    dropped = 0
    for entry in parsed:
        if not isinstance(entry, dict):
            dropped += 1
            continue
        name = entry.get("test_name")
        if not isinstance(name, str) or not name.strip():
            dropped += 1
            continue
        try:
            value = float(entry.get("value"))
        except (TypeError, ValueError):
            dropped += 1
            continue
        test_date = entry.get("test_date")
        if not isinstance(test_date, str) or not test_date.strip():
            test_date = None
        code = entry.get("test_code")
        unit = entry.get("unit")
        items.append(
            {
                "test_name": name.strip(),
                "test_code": code.strip() if isinstance(code, str) and code.strip() else None,
                "value": value,
                "unit": unit.strip() if isinstance(unit, str) and unit.strip() else None,
                "test_date": test_date,
            }
        )

    return {"items": items, "dropped_count": dropped, "error": None}


# Mime types Gemini can read directly as an inline document/image part --
# covers "a PDF or any format of report/document" without any local PDF/OCR
# library: the same multimodal model that already does the text extraction
# reads the file's pixels/pages itself. Shared by the lab-report file upload
# and the trial-document upload below.
SUPPORTED_DOCUMENT_FILE_MIME_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
}


def extract_lab_values(raw_text: str) -> dict:
    """Pasted free-text lab report -> structured lab values. See
    `_extract_lab_values_from_contents` for the shared call/parsing logic."""
    return _extract_lab_values_from_contents(raw_text)


def extract_lab_values_from_file(file_bytes: bytes, mime_type: str) -> dict:
    """Uploaded lab report file (PDF or photo/scan image) -> structured lab
    values. Same Gemini client/model/config as `extract_lab_values` and the
    criteria parser -- only the input part changes, from a text string to
    an inline document/image part, since Gemini reads both natively."""
    part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
    return _extract_lab_values_from_contents([part])


TRIAL_DOCUMENT_SYSTEM_PROMPT = """You are a clinical trial document reader.

You will be given a clinical trial protocol, summary, or registration
document -- as raw text, or as a document (PDF, or a photo/scan of a
printed page). Extract the trial's identifying metadata and its full
eligibility criteria section.

Return ONLY a JSON object, no prose, no markdown fences:
{
  "title": "<the trial's title/name, or null if not stated>",
  "phase": "<the trial phase as stated, e.g. 'Phase 2', or null>",
  "primary_endpoint": "<the primary objective/endpoint/outcome measure, in
    the document's own words, or null if not stated>",
  "eligibility_criteria": "<the FULL eligibility criteria section, copied
    VERBATIM from the document -- both inclusion and exclusion criteria,
    including their headers and every bullet/numbered item. Do not
    summarize, paraphrase, or drop any criterion. If the document has no
    identifiable eligibility criteria section at all, use null.>"
}

Your only job is faithful extraction -- you are not deciding what the
criteria mean or whether they're well-formed; a separate step structures
them afterward. If a field genuinely isn't present in the document, use
null for it rather than guessing or inventing a value.
"""


def extract_trial_document(raw_text: str) -> dict:
    """Pasted/decoded trial document text -> {title, phase,
    primary_endpoint, eligibility_criteria, error}. See
    `_extract_trial_document_from_contents` for the shared logic."""
    return _extract_trial_document_from_contents(raw_text)


def extract_trial_document_from_file(file_bytes: bytes, mime_type: str) -> dict:
    """Uploaded trial document file (PDF or photo/scan image) -> the same
    shape as `extract_trial_document`. Same Gemini client/model as every
    other extraction in this file -- only the input part changes."""
    part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
    return _extract_trial_document_from_contents([part])


def _extract_trial_document_from_contents(contents) -> dict:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return _trial_doc_error("GEMINI_API_KEY is not configured")

    client = genai.Client(api_key=api_key)

    parsed = None
    last_error = None
    for _ in range(2):  # one retry on a transient call/parse failure
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=TRIAL_DOCUMENT_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    temperature=0,
                ),
            )
        except Exception as exc:
            last_error = f"LLM call failed: {exc}"
            continue

        try:
            parsed = json.loads(_strip_markdown_fences(response.text))
            last_error = None
            break
        except (json.JSONDecodeError, TypeError) as exc:
            last_error = f"LLM returned malformed JSON: {exc}"
            parsed = None
            continue

    if parsed is None:
        return _trial_doc_error(last_error)

    if not isinstance(parsed, dict):
        return _trial_doc_error("LLM response was not a JSON object")

    def _clean_str(value):
        return value.strip() if isinstance(value, str) and value.strip() else None

    return {
        "title": _clean_str(parsed.get("title")),
        "phase": _clean_str(parsed.get("phase")),
        "primary_endpoint": _clean_str(parsed.get("primary_endpoint")),
        "eligibility_criteria": _clean_str(parsed.get("eligibility_criteria")),
        "error": None,
    }


def _trial_doc_error(message: str) -> dict:
    return {
        "title": None,
        "phase": None,
        "primary_endpoint": None,
        "eligibility_criteria": None,
        "error": message,
    }


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
