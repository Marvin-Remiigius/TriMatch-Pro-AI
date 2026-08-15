# TriMatch Pro AI — Build Plan

Clinical Trial Matching & Research Assistant (Hackathon Track 4).
An intelligent assistant that matches patients to clinical trials by parsing
trial eligibility criteria into machine-checkable rules and evaluating them
against structured patient records — with a per-criterion, source-cited
explanation for every decision.

## Where things stand (2026-08-16)

**Phase 1** (below) is a complete, working, in-memory demo: 5 synthetic
patients, ClinicalTrials.gov fetch, Gemini-based criteria parsing, a
deterministic matching engine, a ranked candidate dashboard, and an audit
log. It still runs (`uvicorn main:app --reload`) and is a good fallback demo.

**Phase 2** supersedes it as the actual plan going forward. After a demo,
judges asked how this scales to real matching efficiency, daily lab report
ingestion, old-vs-new lab comparison, millions of records, trial progress
tracking, compliance, and a real DB schema tying trials + patients + daily
reports + compliance together. Phase 2 answers those by moving persistence
into Supabase (Postgres) with a real schema, real scale (1000 synthetic
patients + lab results already loaded), and the same audit-first design
principles from Phase 1, applied at that scale. See the **Phase 2** section
below for the schema, judge-question mapping, and current progress.

## How to use this file
Work one step at a time. After each step: run it, confirm it works, then
`git commit`. Do not skip ahead or build multiple steps in one prompt.

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

## Phase 1 — in-memory MVP demo (complete, kept as fallback)

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

### 7. Compliance / audit polish (if time) — DONE
- [x] `audit.py` — every match decision (one per criterion per patient, for
  both `/match` and `/candidates`) is appended to an in-memory log with a
  UTC timestamp, `nct_id` (when known), `patient_id`, `criterion_id`, the
  **source field used** (`field`, e.g. `lab.hba1c`), verdict, patient
  value, and reason. Added `field` to `CriterionMatch` so the matching
  engine's output itself now names the field it checked, not just the
  audit layer.
- [x] `GET /audit-log` — full log, newest first, filterable by
  `nct_id`/`patient_id`/`limit`.
- [x] `GET /flagged-for-review` — same filters, restricted to `unknown`
  verdicts, which is exactly the union of `needs_review` criteria (always
  evaluate to `unknown`, step 4) and missing-patient-data unknowns — no
  separate needs_review store required.
- [x] `MatchRequest` gained an optional `nct_id` so direct `/match` calls
  can still be tied to a trial in the audit trail.
- [x] Tested: two direct `/match` calls (P001, P003) produced 10 audit
  entries, correctly filtered down to the 4 that were actually unknown
  (2 missing-lab-data, 2 needs_review); a real `/candidates` call against
  NCT04280705 logged 100 entries (20 criteria x 5 patients) and
  `/flagged-for-review` correctly returned only the unknown subset.

---

## Status: Phase 1 (steps 0-7) complete and kept as a working fallback demo.

---

## Phase 2 — Supabase-backed trial / enrollment / compliance layer

### Why this exists
Judges asked seven questions Phase 1's in-memory design doesn't answer at
scale. Each maps to a specific table in
`migrations/migration_trial_layer.sql` (already applied to the live
Supabase project, on top of an existing `patients`/`lab_results`/
`diagnoses` schema with 1000 synthetic patients already loaded):

| Judge question | Answered by |
|---|---|
| How patients are matched efficiently | `trials`, `trial_criteria`, `match_results` + indexes on `patients(age)`, `diagnoses(diagnosis_code)`, `lab_results(patient_id, test_code, test_date DESC)` |
| How daily reports are processed | `lab_results` — append-only, one row per report/test |
| How old vs new lab results are compared | `patient_trial.baseline_date` anchors "baseline" vs later `lab_results(test_date)` rows for the same `patient_id` + `test_code` |
| How millions of reports/data are managed | Pre-aggregated `trial_metrics` (dashboards read this, not raw history) + the indexes above, not full-table scans |
| How trial progress is tracked | `trial_metrics` (enrolled, active, dropouts, success_rate) |
| How compliance is maintained | `patient_trial` (consent/enrollment state machine), `match_results.source_lab_result_id` (every verdict cites its source row), `audit_log` (append-only, never updated/deleted) |
| How the DB is structured around trials + patients + daily reports + compliance | The full `migrations/migration_trial_layer.sql` schema |

### Design principle carried over from Phase 1
The LLM's only job is extraction (free text → structured `trial_criteria`
rows). It never decides eligibility. Matching itself stays plain
deterministic Python comparing values against thresholds, same as Phase 1's
`matching.py` — that split is what makes the system auditable, and it's the
strongest answer to the compliance question. Reusing the already-tested
Gemini setup from Phase 1 (`llm.py`) rather than adding a second LLM
provider for this.

### Steps

#### 1. Migration — DONE
- [x] `migrations/migration_trial_layer.sql` — `trials`, `trial_criteria`,
  `patient_trial`, `match_results`, `trial_metrics`, `audit_log`, plus
  indexes. Applied directly via the Supabase SQL editor (idempotent).
- [x] Confirmed live: all 5 new tables plus the pre-existing `patients`
  (1000 rows), `lab_results` (1000 rows), `diagnoses` tables are reachable
  via the anon key.

#### 2. Trial import — DONE
- [x] `db.py` — shared Supabase client (`DATABASE_URL` + `SUPABASE_ANON_KEY`
  via `python-dotenv`, same pattern as `GEMINI_API_KEY`).
- [x] `trials.py`'s `fetch_trial` extended to also pull `primary_endpoint`
  from `outcomesModule.primaryOutcomes[0].measure`.
- [x] `POST /trials/{nct_id}/import` fetches the trial and upserts
  `nct_id`/`title`/`phase` (joined from the phases list)/`status`/
  `primary_endpoint` into `trials` (`on_conflict="nct_id"`); returns the raw
  eligibility criteria text in the response.
- [x] Tested against `NCT04280705`: row upserted correctly, verified by a
  direct Supabase read; re-importing the same trial confirmed true upsert
  (row count stayed at 1, no duplicate). Bad NCT id still 404s before
  touching the DB.
- Note: the migration's `updated_at DEFAULT now()` only fires on insert,
  not update — a re-import doesn't currently bump `updated_at`. Not fixed
  yet (would need a trigger); flagged for later if the "last synced" story
  matters for compliance.

#### 3. Criteria parser → `trial_criteria` — DONE
- [x] `POST /trials/{nct_id}/parse-criteria` reuses Phase 1's
  `llm.py`/`parse_criteria` (Gemini) rather than a second Anthropic-based
  parser. Fetches the trial, upserts its `trials` row first (FK integrity,
  so this endpoint doesn't require a prior `/import` call), runs the
  eligibility text through `parse_criteria`, deletes any existing
  `trial_criteria` rows for the `nct_id`, and inserts the fresh ones
  (`raw_text`/`type`/`field`/`operator`/`value` as JSON-encoded text/
  `unit`/`needs_review`).
- [x] Response: inserted criteria (with real `criterion_id`s from the DB)
  plus total/inclusion/exclusion/needs_review counts.
- [x] Tested against `NCT04280705`: first call hit a transient Gemini
  hiccup and correctly degraded to a single `needs_review` fallback
  criterion rather than crashing (proves the Phase 1 fallback path works
  in the DB-backed flow too); the retry parsed 13 real criteria (8
  inclusion, 5 exclusion, 12 needs_review, 1 clean `eGFR < 30` exclusion).
  Delete-then-insert confirmed idempotent — DB row count stayed at 13
  after the retry, no stale row left from the failed first attempt.
- [x] Ran the sanity check the step called for: parsed `field` values vs.
  real `lab_results.test_code` values. Found a real gap — the existing
  1000-row `lab_results` dataset only had 5 basic tests (Cholesterol,
  Creatinine, Glucose, Hemoglobin, WBC), so labs like HbA1c/eGFR/ALT/AST
  that most trial criteria reference had zero matchable data. Resolved by
  expanding the dataset (see below) rather than constraining the parser,
  since real trials genuinely need those labs.
- Note: `trial_criteria` has no `reason` column in the migration schema, so
  the parser's `needs_review` explanation (e.g. "informed consent is
  procedural, not measurable") is preserved in the Phase 1 in-memory
  `Criterion`/API response but is **not** persisted to the DB. Flagged, not
  fixed — would need an `ALTER TABLE trial_criteria ADD COLUMN reason
  text` migration if the DB-persisted row needs to carry it too.

#### 3a. Lab data expansion — DONE
- [x] `scripts/seed_extra_labs.py` — adds one `lab_results` row per patient
  for HbA1c, eGFR, ALT, AST (matching the existing table's conventions:
  `lab_report_id` format, `reference_min`/`max`, `abnormal_flag` computed
  from the value). Purely additive; reversible via
  `DELETE FROM lab_results WHERE test_code IN ('HBA1C','EGFR','ALT','AST')`.
- [x] Dry-run tested on 2 patients first (confirmed anon-key write access
  and schema fit) before running at full scale; those 2 rows were deleted
  before the real run so nothing was duplicated.
- [x] Ran against all 1000 patients: 4000 new rows inserted in batches of
  500. Verified: `lab_results` now has 5000 total rows, each of the 4 new
  test codes has exactly 1000 rows (one per patient), and eGFR has
  realistic variety for exclusion-criteria demos (35 patients below 30,
  the CKD threshold the COVID trial's exclusion criterion actually uses).

#### 4. Matching engine → `match_results` — NEXT
Reuse Phase 1's `matching.py` logic (already deterministic, already tested)
but read from Supabase instead of the in-memory patient store, and write
each `CriterionMatch` as a `match_results` row with `source_lab_result_id`
set to the actual `lab_results` row that was checked — that FK is the
citation that makes a verdict auditable, not just the `reason` text.

#### 5. Ranked candidates at scale — NEXT
Coarse SQL filter first (indexed `age`/`diagnosis_code` range/equality
checks) to cut 1000 patients down before running the deterministic
per-criterion evaluation on the survivors — this is what makes "efficient
matching" a demo, not a claim.

#### 6. `trial_metrics` + progress dashboard — NEXT
Incrementally updated `enrolled`/`active`/`dropouts`/`success_rate` per
trial, read by the dashboard instead of aggregating `match_results`/
`patient_trial` on every page load.

#### 7. `patient_trial` enrollment/consent flow — NEXT
Invitation → accept/decline → consent → enrolled → withdrawn state machine.
`baseline_date` gets set at enrollment and anchors old-vs-new lab
comparisons against later `lab_results` rows.

#### 8. Researcher/patient portal — NOT STARTED, large scope
The full 19-step flow (researcher login, protocol upload, invitations,
patient accept/decline, document review, lab upload for source data
verification, T&Cs, participation confirmation, new lab reports during the
trial, baseline-vs-latest comparison, cumulative deviation, phase
advancement) is the long-term target this schema supports, but is a much
bigger build (auth, two user roles, file uploads) than anything above.
Not scoped in detail yet — revisit once steps 3-7 are solid.

#### 9. `audit_log` (DB-backed) — NOT STARTED
Phase 1's `audit.py` is in-memory and process-local. Migrating match
decisions (and eventually consent actions) into the real `audit_log` table
makes the audit trail durable and queryable outside the app process — do
this once match results are actually being written to `match_results`
(step 4), so there's real data to log.

---

## Demo narrative (what to show judges)
1. Paste a real trial's messy eligibility text → watch it become clean rules.
2. Show a ranked list of patients for that trial.
3. Expand one patient → every criterion with pass/fail/unknown + why.
4. Point out an `unknown` and explain: the system flags missing data instead
   of guessing — the coordinator decides. That's the compliance story.
5. (Phase 2) Point at `match_results.source_lab_result_id` and explain: every
   verdict cites the exact lab row that justified it, not just a reason
   string — that's source data verification, not just an audit note.

## Guardrails / notes to self
- Commit after every working step.
- Turn OFF auto-accept edits before touching the matching engine so you
  review changes to core logic.
- All patient data is synthetic — no real PHI, ever.
- Keep the LLM scoped to extraction only; matching stays deterministic
  Python. Don't let a second LLM provider creep in for something Gemini
  already does (parsing).
