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

#### 4. Matching engine → `match_results` — DONE
- [x] `db_matching.py` — reuses Phase 1's pure comparison functions
  (`_normalize_op`, `_evaluate_condition` imported from `matching.py`, not
  duplicated) with a new Supabase-backed field resolver
  (`resolve_db_field`), so the actual comparison logic is identical between
  Phase 1 and Phase 2, only where the data comes from differs.
- [x] Field resolver mapping (Phase 1 field name -> real Supabase source):
  `age`/`sex` -> `patients.age`/`patients.gender` (note: DB column is
  `gender`, not `sex` -- aliased in the resolver so the parser's field
  whitelist didn't need to change); `diagnosis.icd10`/`diagnosis.label` ->
  `diagnoses` table; `medication` -> `medications.drug_name`; `lab.<name>`
  -> `lab_results` filtered by `test_code` (most recent by `test_date`,
  with an alias map for spelled-out names like `lab.cholesterol` -> `CHOL`
  since the parser doesn't always emit the short code); `vitals.<name>` ->
  `vital_signs`, via an explicit column map (`spo2` ->
  `oxygen_saturation`, `temperature_c` -> `temperature`, etc.) since that
  table's column names don't match Phase 1's convention either.
- [x] `POST /trials/{nct_id}/match/{patient_id}` evaluates every
  `trial_criteria` row for the trial against one real patient, computes the
  overall verdict (same any-fail/any-unknown/else-eligible logic as Phase
  1), and writes `match_results` rows (delete-then-insert, same idempotent
  pattern as step 3) with `source_lab_result_id` set whenever the
  criterion resolved through a `lab.*` field.
- [x] 404s if the patient doesn't exist, and 404s with a clear message if
  the trial has no parsed criteria yet (tells the caller to call
  `/parse-criteria` first) rather than silently matching against nothing.
- [x] Tested against `NCT04280705` with two real patients: one with eGFR
  16.05 (well under the trial's `< 30` exclusion threshold) correctly came
  back `fail` on that criterion -> overall `ineligible`; one with eGFR
  103.9 correctly `pass` -> overall `needs more data` (everything else on
  this trial is `needs_review`). Verified in the DB directly:
  `match_results.source_lab_result_id` for the low-eGFR patient's
  criterion pointed at the exact `lab_results` row (1182) that produced
  the verdict -- the compliance/source-data-verification citation works
  end to end, not just in the reason text. Re-matching the same patient
  confirmed idempotency (still 13 rows, no duplicates). Both error paths
  (unknown patient, unparsed trial) 404 correctly.
- Scope note: this matches one patient at a time, correct but not yet
  optimized for 1000 patients — batching/coarse-filtering is step 5.

#### 5. Ranked candidates at scale — DONE
- [x] `coarse_filter.py` — narrows the candidate pool via indexed SQL
  before the expensive per-criterion evaluation runs, for the two
  criterion types the plan called out: `age` range/equality, and a
  required `diagnosis.icd10` (inclusion + contains/in/==). Deliberately
  conservative: only narrows on criteria where the outcome is unambiguous
  (never "unknown"), so under-filtering (skipping a criterion type this
  module doesn't handle) is always safe — the full matcher still evaluates
  it correctly afterward. Exclusion-type criteria are handled by inverting
  the operator (`>` becomes `<=`, etc.), mirroring the same inclusion/
  exclusion inversion rule proven in step 4, not a new rule.
- [x] `match_patient_db` (step 4) extended to accept pre-fetched
  `criteria_rows` so matching many patients against one trial doesn't
  re-fetch the identical criteria list per patient.
- [x] `GET /trials/{nct_id}/db-candidates` (`limit`, `max_evaluate` query
  params) — coarse-filters, then fully matches (and writes `match_results`
  for, via step 4's function) each survivor up to `max_evaluate`, ranks
  them (same key as Phase 1: overall bucket, then passes, unknowns,
  fails), and returns a lightweight summary list (no full per-criterion
  breakdown — that's a separate `/match/{patient_id}` call, step 4) along
  with `total_patients`/`coarse_filtered_count`/`evaluated_count`/
  `returned` so nothing is silently dropped from view.
- [x] Caught and fixed a real bug during testing: `patients.age` is a
  Postgres `integer` column, and passing a Python float (`18.0`) 500'd
  with `invalid input syntax for type integer`. Fixed by casting to `int`
  for the age comparison.
- [x] Verified the coarse filter's correctness against direct SQL
  cross-checks, not just "it returns 200": a synthetic trial with age
  18-40 (308 patients) AND required diagnosis I10 (120 patients) narrowed
  to exactly 43 candidates, and every one of them came back `eligible`
  with all 3 criteria passing — the coarse filter's guarantee matched the
  full matcher's actual verdict. A second synthetic trial (exclusion `age
  > 65`) narrowed to exactly 662 (`patients.age <= 65`, cross-checked via
  direct SQL), confirming the exclusion-inversion path too. Both test
  trials cleaned up (`trials`/`trial_criteria`/`match_results` rows
  deleted) after verification. Full regression across every prior
  endpoint (Phase 1 and Phase 2) passed.
- Known limitation, not fixed: `NCT04280705`'s only clean criterion is
  `lab.egfr` (not age/diagnosis), so the coarse filter doesn't narrow it
  at all — falls back to all 1000 patients, capped by `max_evaluate`
  (default 200). The filter only helps trials whose clean criteria happen
  to include age/diagnosis; broader coverage (labs, other operators) is a
  possible future enhancement, not built now.

#### 5a. Researcher dashboard (single screen) — DONE
- [x] `static/researcher.html` — a single, dependency-free HTML/CSS/JS page
  (dark, restrained, hairline dividers, mono type for all data/IDs/
  citations, muted verdict colors) served alongside Phase 1's dashboard by
  the same `StaticFiles` mount, at `/researcher.html`. Consumes only
  existing endpoints (`GET /trials/{nct_id}`, `GET
  /trials/{nct_id}/db-candidates`, `POST /trials/{nct_id}/match/
  {patient_id}`) — no new routes, no DB schema changes.
- [x] Trial header (NCT id input, title/phase/status, inline calm error for
  a bad id) → ranked candidate list (funnel stat, mono tally, verdict
  badge, age/sex) → click-through per-criterion detail panel (grouped
  inclusion/exclusion, verdict pill, patient value, source citation,
  reason).
- [x] Two small **additive** backend changes, made deliberately and flagged
  rather than done silently, because the design required data the API
  didn't yet surface: `CriterionMatch` gained `source_lab_result_id`
  (folded directly into `db_matching.evaluate_db_criterion`'s return value,
  which also simplified `match_patient_db` — no more parallel `sources`
  list); `DBCandidateSummary` gained `age`/`sex`, populated via one bulk
  `patients` lookup per `/db-candidates` call (not N+1). No endpoint
  URLs/methods changed, no DB columns added — both are backward-compatible
  response fields.
- [x] Tested live in a real browser (Claude in Chrome), per the brief's own
  test requirement: loaded NCT04280705, confirmed the funnel stat
  ("150 / 1000 patients evaluated · 1000 matched the coarse filter"),
  clicked into a candidate and confirmed needs_review criteria render
  `UNKNOWN` (amber); used the page's own `selectCandidate()` function to
  jump straight to the known low-eGFR patient (`f67b56e8...`, eGFR 16.05)
  and confirmed that criterion renders `FAIL` (red) with `value 16.0504`
  and `source lab_result #1182` — matching the DB row verified in step 4 —
  visually distinct from the amber unknowns on the same screen. Also
  confirmed the inline error state for a bad NCT id (no raw JSON, no
  alert()).
- Known gap: the mobile breakpoint (`@media max-width: 880px`, grid
  collapses to one column) uses a standard, well-tested CSS pattern but
  wasn't pixel-verified in a real narrow viewport — the browser-automation
  resize didn't propagate to the screenshot tool in this session.

#### 6. `trial_metrics` + progress dashboard — DONE
- [x] **Dependency gap found and fixed before building anything**: this
  step needs enrolled patients with a `baseline_date`, but step 7
  (the real invite/consent/enroll flow) hasn't been built, so
  `patient_trial` was completely empty — and Step 3's lab seed gave every
  patient exactly one reading per test, so there was zero real
  baseline-vs-latest data anywhere in the dataset either. Confirmed both
  gaps by direct query before writing code, same discipline as the step 3
  lab-data gap.
- [x] `scripts/seed_enrollment.py` — enrolls a 24-patient synthetic cohort
  into `NCT04280705` (22 enrolled, 2 withdrawn) and inserts a follow-up lab
  reading 60-150 days after each one's baseline, for real variety: 17
  tracked on EGFR, 3 on HBA1C, 1 on WBC (no defined direction, for a real
  `indeterminate`-capable case), 3 enrolled with **no** follow-up (real
  `no_data` cases), values perturbed with genuine improve/worsen mix (not
  all one direction). Purely additive, reversible (delete instructions in
  the script's docstring). **Caught and fixed a real bug in the seed
  itself**: the first attempt found zero unused HBA1C/WBC patients because
  Step 3 seeded all 4 new test types per patient in the same loop, so
  early rows of every test_code belong to the same patients — a `.limit(10)`
  scan kept re-finding patients already claimed by the EGFR pick and
  silently added zero HBA1C patients (success_rate for HBA1C came back
  `0.0` because there was no real data behind it). Fixed by widening the
  scan window and adding an assertion so this fails loudly instead of
  silently next time; re-verified before moving on.
- [x] `progress.py` — `TEST_DIRECTION` lookup (only 8 tests have a defined
  higher/lower-is-better direction; anything else, including a genuinely
  ambiguous one like WBC, is `indeterminate`, never guessed).
  `get_patient_test_progress` computes baseline (nearest reading on/before
  `baseline_date`) vs. latest (most recent reading) per test_code, with
  both source `lab_result_id`s. Deliberately treats "baseline and latest
  are literally the same row" as `no_data` with `latest` fields nulled out
  — showing a "0 deviation" there would misleadingly imply a real
  follow-up comparison had been made when none exists yet.
- [x] `success_rate` is explicitly defined and documented in a code
  comment, not dressed up as rigorous: the fraction of **all** enrolled
  patients (denominator never excludes no_data/indeterminate/withdrawn, to
  avoid inflating the number) whose status on one specific test_code is
  `improved`. That test_code is never guessed from the trial's free-text
  `primary_endpoint` (e.g. "Time to Recovery" has no lab-test mapping) —
  it's either passed explicitly (`?primary_test_code=`) or auto-selected as
  whichever direction-defined test has the most enrolled-patient coverage,
  and the response always names which one was actually used
  (`primary_test_code_used`) so nothing is hidden.
- [x] `GET /trials/{nct_id}/progress` (live, always recomputed — no
  staleness) and `POST /trials/{nct_id}/compute-metrics` (same computation,
  additionally upserts the headline into `trial_metrics`, matching the
  existing table's columns exactly — no schema change).
- [x] Frontend: extended `static/researcher.html` (not a separate page)
  with a "Trial progress" tab, reusing the exact same CSS tokens/mono
  treatment/verdict-color variables as the candidates view — no second
  visual style. Headline metrics with success_rate as the one
  accent-colored, larger figure; an explicit disclaimer sentence
  ("measures the treatment cohort's trajectory against its own baseline...
  not a controlled efficacy result... no control arm") always shown, not
  just documented; per-patient compact `TEST value → value` readouts with
  colored dots (pass-green/fail-red/muted-grey, reusing the matcher
  screen's palette); click-through to full per-test detail with both
  source citations.
- [x] Tested live in a real browser, per the brief's own test requirement:
  selected an enrolled patient with 2 real EGFR readings at different
  dates and confirmed the rendered baseline→latest readout (70.69 →
  64.20 mL/min/1.73m2, deviation -6.4873), the direction call (`WORSENED`,
  red), and both source citations (`lab_result #1010` baseline,
  `lab_result #5027` latest). Confirmed the same patient's other 4 tests
  (missing a baseline or a follow-up, by design) all render `NO DATA`
  (grey, dash placeholder) rather than a fabricated deviation — both
  directions of the no_data case are visually distinguishable from a real
  improved/worsened call. Full regression across every prior endpoint
  passed.
- Known limitation: a transient `Server disconnected` error hit Supabase
  once during testing while the candidates view's ~450-query match loop
  was still running and the progress fetch was triggered concurrently —
  the server itself stayed up and the retry succeeded; this is REST
  connection-pool contention under concurrent load, not a logic bug, and
  is the same underlying scale characteristic already flagged in step 5.

#### 7. `patient_trial` enrollment/consent flow — DONE
- [x] `enrollment.py` — the real state machine, superseding step 6's
  bootstrap seed script for anything going forward: `invite` (creates the
  `patient_trial` row, rejects if one already exists), `consent` (only
  from `invited`; internally two audit-logged transitions,
  invited→accepted→consented, so terms are provably shown while status
  was still `invited` before consent is recorded), `enroll` (only from
  `consented`; sets `enrolled_at` **and `baseline_date`** together — this
  is exactly the anchor step 6 was manually seeding, so real enrollments
  now feed real progress tracking with no gap), `withdraw` (from any
  active state), `decline` (from `invited`/`accepted`). Illegal jumps
  raise `InvalidTransitionError` → HTTP 409 with a clear message naming
  the actual current status, not a silent allow.
- [x] Every transition appends one immutable row to `audit_log`
  (actor/action/entity/entity_id/detail as jsonb with
  from_status/to_status/timestamp) — plain inserts only, never an update
  or delete. `consent` writes two rows (`patient.accepted` then
  `consent.recorded`) so the audit trail shows the actual two-step
  transition, not a collapsed one. Researcher-triggered actions
  (invite/enroll/withdraw) are logged with `actor='researcher'`;
  patient-triggered ones (accept/consent/decline) with `actor='patient'`
  — a real, honest distinction, not decorative.
- [x] Five transition endpoints
  (`POST /trials/{nct_id}/patients/{patient_id}/{invite,consent,enroll,
  withdraw,decline}`), `GET /trials/{nct_id}/enrollment` (current status
  per patient), and `GET /trials/{nct_id}/audit` (the compliance artifact,
  actually queryable, not just claimed).
- [x] `static/consent.html` — the one patient-facing screen, matching the
  dashboard's exact tokens. Reads `?patient=&trial=`, shows trial summary
  + terms + Accept/Decline, handles every state honestly (no invitation
  found; already responded, with the specific status; success; declined),
  and is safe to reload (re-shows "already responded" rather than
  double-submitting). Explicitly labeled "a prototype consent screen for
  demonstration — not a legally binding consent form."
- [x] Extended `static/researcher.html` with an **Enrollment** tab: the
  ranked candidate list (reusing `/db-candidates`) overlaid with each
  patient's real enrollment status and the one action appropriate to that
  status (Invite / open the consent screen / Enroll / Withdraw), plus the
  trial's audit trail rendered as a table (color-coded by actor) right
  there on the same screen — satisfying "surface it somewhere viewable"
  directly, no separate page needed.
- [x] **Caught and fixed a real performance bug during testing**: the
  Enrollment tab's action buttons were re-running the full ~450-query,
  30-60s candidate match loop just to reflect a single status change.
  Fixed by caching the candidate list client-side per trial (`/enrollment`
  and `/audit` are cheap and always refetched; `/db-candidates` only
  refetches on a genuinely new trial) — actions now complete in ~3s.
- [x] Tested end to end, twice — once via direct API calls (curl) to
  verify the raw mechanics, once via the actual UI (Claude in Chrome) to
  verify the real experience:
  - Illegal transition rejected: `enroll` before any invite/consent →
    `409`, `"Cannot enroll: patient must be 'consented' first; current
    status is 'not invited'."` Confirmed again mid-flow (still `invited`,
    not yet `consented`) — also correctly `409`.
  - Full walk: invite → consent → enroll, each transition returning the
    updated record with the right timestamp set (`invited_at`,
    `consented_at`, `enrolled_at` **and `baseline_date`** together on
    enroll). Verified all 4 `audit_log` rows directly in the DB: correct
    order, correct actors (`researcher`/`patient`/`patient`/`researcher`),
    correct `from_status`/`to_status`/`timestamp` in each `detail`.
  - Loop closed: the newly-enrolled patient immediately appeared in step
    6's `/progress` (enrolled count incremented, correct `baseline_date`,
    existing labs correctly `no_data` since no follow-up reading exists
    yet for them).
  - UI walk repeated the same flow live: clicked Invite → opened the real
    consent screen in a new tab → clicked Accept & consent → saw the
    success message → reloaded the consent screen and confirmed it showed
    "already responded" instead of re-submitting → back on the dashboard,
    clicked Enroll → confirmed `ENROLLED` status, the new audit row, and
    the enrolled count increment on the Trial progress tab, all live.
- Known gap: `declined` has no dedicated timestamp column in the existing
  schema (only `invited_at`/`consented_at`/`enrolled_at`/`withdrawn_at`
  exist) — a decline only updates `status` and `updated_at`. Not fixed,
  since the brief said not to change the DB schema; flagged instead.

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
