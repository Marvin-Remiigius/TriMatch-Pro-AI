# TriMatch Pro AI — Build Plan

Clinical Trial Matching & Research Assistant (Hackathon Track 4).
An intelligent assistant that matches patients to clinical trials by parsing
trial eligibility criteria into machine-checkable rules and evaluating them
against structured patient records — with a per-criterion, source-cited
explanation for every decision.

## How to use this file
Work one step at a time. After each step: run it, confirm it works, then
`git commit`. Do not skip ahead or build multiple steps in one prompt.
Steps 3 and 5 are the core of the project — keep everything else simple.

---

## Architecture (target)

```
ClinicalTrials.gov API ──► Trial fetch ──► Criteria parser (LLM) ──► Structured rules
                                                                          │
Synthetic patient records ──► Patient schema ─────────────────────────────┤
                                                                          ▼
                                                              Matching engine
                                                        (pass / fail / unknown
                                                         per criterion + reason)
                                                                          │
                                                                          ▼
                                                        Ranked candidate dashboard
                                                          (human-in-the-loop review)
```

Design principles that win this track:
- **Explainability** — every match shows *why*, criterion by criterion.
- **Human-in-the-loop** — never auto-enroll; surface ranked candidates for review.
- **Unknown ≠ ineligible** — missing data is flagged, never assumed pass or fail.
- **Auditability** — each decision links back to the source record field.

---

## Steps

### 0. Project scaffold — DONE
- [x] venv, FastAPI app, `/health` returns `{"status": "ok"}`
- [x] requirements.txt (pinned), .gitignore, git init + first commit

### 1. Fetch a trial and its criteria — DONE
- [x] `GET /trials/{nct_id}` calls the ClinicalTrials.gov API v2 and returns
  title, phase, overall status, and raw eligibility criteria text.
- [x] `httpx` added to requirements.txt.
- [x] Returns a proper 404 (upstream returns 400 for a bad NCT ID; mapped
  both 400 and 404 from upstream to a 404 here).
- [x] Tested with `NCT04280705` (real) and `NCT00000000` (nonexistent).

### 2. Patient schema + synthetic data — DONE
- [x] `Patient` model (Pydantic, in `models.py`): id, age, sex, diagnoses
  (ICD-10 + label), labs (name, value, unit, date), medications, vitals.
- [x] 5 hand-written synthetic patients in `data/patients.json`, covering
  diabetes, COVID-19, asthma, CKD, and pregnancy cases (deliberately chosen
  to exercise inclusion/exclusion logic later — e.g. P004's low eGFR and
  P005's pregnancy are built to fail common exclusion criteria).
- [x] Loaded into an in-memory store (`patients.py`, `lru_cache`-backed).
- [x] `GET /patients` and `GET /patients/{id}` added to verify the store;
  404 confirmed for an unknown id.

### 3. Criteria parser — CORE COMPONENT — DONE
- [x] `POST /parse-criteria` (`{"text": "<raw eligibility text>"}`) uses
  Gemini (`gemini-2.5-flash`, `llm.py`) to extract structured rules.
- [x] JSON mode (`response_mime_type: application/json`) plus a fallback
  `Criterion` with `needs_review=True` for LLM call failures, malformed
  JSON, or items that fail Pydantic validation.
- [x] Criteria that are compound/subjective/procedural are flagged
  `needs_review: true` with a `reason`, not guessed.
- [x] Tested against a hand-written mix (clean + vague criteria) and the
  real NCT04280705 eligibility text end-to-end: clean criteria like
  `eGFR < 30` structured correctly; compound/subjective ones (informed
  consent, "symptoms suggestive of", multi-condition OR blocks) correctly
  flagged for review instead of guessed.
- Note: using Gemini instead of Claude for this project (free-tier key
  available); `GEMINI_API_KEY` required in `.env` (see `.env.example`).

### 4. Matching engine — CORE COMPONENT — DONE
- [x] `POST /match` (`{"patient_id", "criteria"}`, `matching.py`) evaluates
  each criterion deterministically in plain Python (no LLM) and returns
  `pass`/`fail`/`unknown`, the resolved `patient_value`, and a reason.
- [x] Field resolver supports `age`, `sex`, `diagnosis.icd10`,
  `diagnosis.label`, `medication`, `lab.<name>` (most recent by date),
  `vitals.<name>`; missing data → `unknown`, never guessed.
- [x] Operators: `>`, `>=`, `<`, `<=`, `==`, `!=`, `in`, `not_in`,
  `contains`, `not_contains` (plus common aliases like `=`/`gte`).
- [x] Exclusion semantics inverted from inclusion: for an exclusion
  criterion, the disqualifying condition being *true* → `fail` (blocks the
  patient); condition *false* → `pass` (clears it).
- [x] `needs_review` criteria from step 3 always evaluate to `unknown`,
  carrying the parser's `reason` through untouched.
- [x] Overall verdict: any `fail` → `ineligible`; else any `unknown` →
  `"needs more data"`; else `eligible`.
- [x] Tested a 5-criterion set (2 clean inclusions, 1 needs_review
  inclusion, 1 clean exclusion, 1 contains-based exclusion) against all 5
  synthetic patients — verified P002 fails on HbA1c, P004 and P005 are
  excluded (CKD eGFR, pregnancy diagnosis via `contains`), P003's missing
  labs correctly surface as `unknown`/"needs more data" rather than a
  guessed fail, and an unknown patient_id 404s.

### 5. Ranked candidate view — NEXT
`GET /trials/{nct_id}/candidates` that runs every patient against a trial and
returns them ranked (e.g. by number of criteria passed, unknowns as a
tiebreaker), each with the per-criterion breakdown from step 4.
- This is what a trial coordinator would actually look at.

### 6. Frontend dashboard (if time)
A simple page: pick a trial → see ranked candidates → expand a candidate to
see every criterion, its verdict, the source value, and the reason.
- Plain HTML/JS served by FastAPI, or a small React app — keep it lightweight.
- The expandable per-criterion breakdown IS the demo. Make that legible.

### 7. Compliance / audit polish (if time)
- Log every match decision with a timestamp and the source field used.
- Add a "flagged for review" list aggregating all `needs_review` criteria
  and all `unknown` verdicts across patients.

---

## Demo narrative (what to show judges)
1. Paste a real trial's messy eligibility text → watch it become clean rules.
2. Show a ranked list of patients for that trial.
3. Expand one patient → every criterion with pass/fail/unknown + why.
4. Point out an `unknown` and explain: the system flags missing data instead
   of guessing — the coordinator decides. That's the compliance story.

## Guardrails / notes to self
- Commit after every working step.
- Turn OFF auto-accept edits before touching the matching engine (step 4) so
  you review changes to core logic.
- All patient data is synthetic — no real PHI, ever.
- Don't gold-plate steps 1, 2, 6. Time goes to 3, 4, 5.
