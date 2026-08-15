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

### 2. Patient schema + synthetic data — NEXT
A `Patient` model (Pydantic) with: id, age, sex, diagnoses (ICD-10 codes +
labels), labs (name, value, unit, date), medications, key vitals.
- Hand-write 3–5 synthetic patients as JSON so you control the test cases.
- (Stretch) generate realistic FHIR data with Synthea later.
- Load them from a JSON file into an in-memory store for now.

### 3. Criteria parser — CORE COMPONENT
`POST /parse-criteria` that takes raw eligibility text and uses an LLM to
extract a list of structured rules, each like:
```json
{"id": "c1", "type": "inclusion", "text": "HbA1c > 7.0%",
 "field": "lab.hba1c", "operator": ">", "value": 7.0, "unit": "%"}
```
- Prompt the LLM to return ONLY JSON; parse safely, handle malformed output.
- Mark criteria it can't structure as `"needs_review": true` rather than
  guessing — these get surfaced to a human, not silently dropped.
- This is the "wow" component. Spend your time here.

### 4. Matching engine — CORE COMPONENT
`POST /match` that takes a patient + a trial's structured rules and returns,
for each criterion: `pass` / `fail` / `unknown`, the patient value used, and
a one-line reason.
- Evaluation is **deterministic** (plain Python rule checks), not LLM — this
  is what makes it auditable. LLM is only for parsing (step 3).
- If the patient lacks the data a criterion needs → `unknown`, never `fail`.
- Overall verdict: eligible only if all inclusions pass and no exclusion
  fails; otherwise `ineligible` or `needs more data`.

### 5. Ranked candidate view
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
