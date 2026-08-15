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

### 5. Ranked candidate view — DONE
- [x] `GET /trials/{nct_id}/candidates` fetches the trial (`trials.py`,
  factored out of the step-1 handler), parses its eligibility text once
  (step 3), then runs every synthetic patient through the matching engine
  (step 4) and returns them ranked, each with its full per-criterion
  breakdown.
- [x] Ranking key: overall bucket first (`eligible` < `needs more data` <
  `ineligible`), then more passes first, then fewer unknowns, then fewer
  fails, as a tiebreak.
- [x] Response also includes the parsed criteria list (so a client doesn't
  need a second `/parse-criteria` call to show what the ranking is based
  on).
- [x] Tested against NCT04280705: unknown NCT id still 404s; real trial
  returns 20 parsed criteria and 5 ranked candidates in the exact order
  the sort key predicts (verified by hand against pass/unknown/fail
  counts) — all `ineligible` here since none of the synthetic patients
  have lab-confirmed SARS-CoV-2, which is itself a correct result, not a
  bug.

### 6. Frontend dashboard (if time) — DONE
- [x] `static/index.html` — a single-file, dependency-free HTML/JS/CSS page
  (no build step) served by FastAPI via `StaticFiles` mounted at `/`
  (mounted last, after all API routes, so it never shadows them).
- [x] Flow: enter an NCT ID → `GET /trials/{id}/candidates` → trial header
  + ranked candidate cards (patient id, overall badge, pass/fail/unknown
  counts) → click a card to expand a full criterion table (id, type,
  criterion text, color-coded verdict, patient value, reason).
- [x] Loading state (LLM parse can take up to ~30s) and an inline error
  panel for bad NCT ids.
- [x] Verified live in a real browser (not just curl) via Claude in
  Chrome: loaded NCT04280705, confirmed the ranked list matches the API
  order, expanded P001's breakdown (readable, color-coded, legible), and
  confirmed the error panel for a bad NCT id.

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
