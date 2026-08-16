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
- Follow-up finding while hunting for a "fast-loading" real trial to
  recommend: the dashboard's `max_evaluate=150` was hardcoded regardless
  of `coarse_filtered_count`, so a trial that coarse-filtered to 353
  patients (`NCT04212468`, clean `age >= 65`) actually took *longer*
  (97s) than the unfiltered `NCT04280705` (30-40s) — Supabase network
  variance dominated, not the filter. The filter's speed benefit only
  materializes once `max_evaluate` is raised past the filtered count;
  capped low, wall-clock time is roughly constant across trials. Lowered
  the dashboard's default to `limit=30&max_evaluate=30` (verified: ~16s),
  trading a smaller per-load sample for predictable speed.

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

## Compound-criteria decomposition (2026-08-16) — DONE

Standalone, additive change on top of Phase 1 + Phase 2's parser and
matcher, done as its own commit per explicit instruction so it reverts
cleanly. Motivating problem: many real criteria bundle an independent,
easily-checkable condition (an age bound) together with something genuinely
unstructurable (an OR across sex/pregnancy, or a multi-branch "at least one
of" list) in one sentence — the old parser could only structure a criterion
fully or not at all, so the checkable part got thrown away along with the
unstructurable part.

**No DB schema change.** `trial_criteria` has no column for a rules list.
A criterion with 0-1 rules is encoded exactly as before (a single scalar in
the `value` column); a criterion with 2+ rules stores the full rule list as
JSON in that same column, with `field`/`operator`/`unit` mirroring the
first rule for backward compat with anything (`coarse_filter.py`) that only
reads the top-level columns. The matcher detects the shape (`_is_rule_list`)
and branches accordingly, so old rows and new single-rule rows hit the
identical code path as before.

- [x] `models.py`: `Rule`, `RuleResult`, `Criterion.rules` (additive,
  defaults `None`), `CriterionMatch.rule_results` (additive). A
  `model_validator` mirrors `rules[0]` into the legacy top-level
  `field`/`operator`/`value`/`unit` whenever they're otherwise unset, so
  anything still reading those directly keeps working.
- [x] `matching.py` / `db_matching.py`: `evaluate_criterion` /
  `evaluate_db_criterion` rewritten around a per-rule evaluator + Kleene-AND
  combination (`_effective_rules`/`_effective_db_rules` fall back to a
  single legacy rule ONLY when field+operator+value are all present —
  matching the old gate exactly). Verdict logic: any known-false sub-rule
  → the compound condition is false regardless of other unknowns (AND with
  false is false); else any unknown sub-rule → unknown; else true. Same
  inclusion/exclusion inversion as before, applied once to the combined
  result. **Partial-criterion honesty guard**: if `needs_review` is still
  true because part of the criterion is genuinely unstructurable, a
  would-be "pass" is downgraded to `unknown` (the checked part alone can't
  confirm the whole original criterion) — but a genuine "fail" from the
  checked part is still reported as-is (safe regardless of whether the true
  relationship to the unstructured remainder is AND or OR, since one
  confirmed disqualifying condition is always sufficient).
- [x] `llm.py`: prompt rewritten around a `rules: [...]` array (replacing
  top-level field/operator/value in the JSON the model emits), with
  explicit OR-guidance and three worked examples: (1) partial structuring
  --- age captured, sex/pregnancy OR left unstructured (the exact case from
  the brief); (2) full multi-rule AND (eGFR + age); (3) a multi-branch "at
  least one of" OR list staying fully unstructured even though one branch
  looks simple.
- [x] `main.py`: `_criterion_to_db_row` helper encodes 0-1-rule criteria
  identically to before; 2+-rule criteria get the new JSON-list encoding.
  No other endpoint changed.
- [x] Verified backward compatibility directly in Python (not just via the
  API) before touching anything live: old-style single-rule construction
  vs. new-style 1-element `rules` list produce byte-identical verdict and
  reason strings, for both inclusion and exclusion criteria, in both
  `matching.py` and `db_matching.py` (the latter against a real Supabase
  patient).
- [x] Verified the new AND/Kleene logic directly: both-true → fail
  (exclusion triggers), one-false → pass (short-circuits correctly even
  when the OTHER rule is unknown — a known-false always wins over an
  unknown), both-unknown-with-one-true → unknown.
- [x] Verified the partial-downgrade: a criterion with one structured
  passing sub-rule and a genuinely unstructured remainder → `unknown`, not
  a false pass; the same criterion with the sub-rule failing → still
  `fail`, not softened.
- [x] Live re-parse of `NCT04280705`: the exact "Male or non-pregnant
  female adult >= 18" criterion now yields a real `age >= 18` sub-rule that
  evaluates a different real value per patient (confirmed against two real
  Supabase patients, ages 45 and 71) while staying `needs_review` for the
  unstructured sex/pregnancy part; "provides informed consent" still
  resolves to zero rules, fully unstructured — no overreach.
- [x] **Found and fixed a real issue during this same verification pass**:
  the first live re-parse pulled a single branch (SpO2 <= 94) out of a
  4-way "at least one of" OR-list as if it were an independent rule — a
  patient failing that one branch could still satisfy the criterion via
  another branch never checked, so reporting "fail" there would have been
  wrong (unlike the AND case, a single false OR-branch doesn't determine
  the outcome). Added explicit prompt guidance + a third worked example;
  re-verified the fix removes the single-branch extraction (that criterion
  now correctly resolves to zero rules) without touching the criteria that
  were already correct.
- [x] Confirmed the dashboard needed **zero code changes** — existing
  `verdict`/`patient_value`/`reason` fields already carry a sensible
  summary for multi-rule and partial criteria (verified live in a real
  browser: the partial-criterion reason text and patient value render
  correctly with no layout breakage).
- [x] Full regression pass on every endpoint (Phase 1 and Phase 2
  dashboards, `/candidates`, `/progress`, `/enrollment`, `/audit`,
  `/audit-log`, `/flagged-for-review`) after the change — all 200.
- Known, pre-existing, unrelated observation: Gemini's free-tier daily
  quota (20 requests/day for `gemini-2.5-flash`) was exhausted mid-session
  purely from cumulative testing across this whole project — the existing
  "LLM call failed" fallback (from step 3) degraded safely both times
  rather than crashing, which is itself a confirmation that fallback path
  still works correctly under the new schema. Not something this change
  caused or can fix; a new key resolved it.
- Known limitation, not addressed here: `trial_criteria` still has no
  `reason` column (same pre-existing gap noted under step 3), so a
  criterion's needs_review explanation lives in the live API response but
  isn't persisted to the DB for multi-rule criteria either.

---

## Hero trials: NCT07348718 + NCT04791358 (2026-08-16) — DONE

Imported and evaluated two real ClinicalTrials.gov trials against the
1000-patient dataset, purely to find which one produces the richest
candidate screen for demos. Additive only: two new `trials` rows + their
`trial_criteria` rows, using the existing import/parse/match pipeline. No
DB schema change, no matcher logic change. Both trials are kept — neither
was deleted.

- **Trial A — NCT07348718 (Rubix LS Diabetic Kidney Disease Registry)**:
  parsed into 9 criteria (3 structured: age >= 18, T2DM-required inclusion,
  T1DM exclusion; 6 needs_review). Its real CKD/eGFR criterion text is
  "eGFR <60 ... and/or UACR >=30" — a genuine OR against a lab we have no
  UACR data for, so it correctly resolves to zero rules (needs_review,
  unknown) rather than guessing — this is the OR-safety fix from the
  compound-criteria change working correctly on new real-world text, not a
  bug. Evaluated 30/30 sampled patients: 100% `ineligible`, with every
  single patient landing on the *identical* shape (pass=2, fail=1,
  unknown=6) — driven entirely by the T2DM-required inclusion gate (only
  ~106/1000 patients are T2DM). eGFR never contributes any signal at all.
- **Trial B — NCT04791358 (KidneyIntelX Decision Impact Trial)**: parsed
  into 11 criteria (5 structured, 6 needs_review). Its "eGFR 30-60" text
  parsed cleanly into a real 2-rule AND (`lab.egfr >= 30` AND
  `lab.egfr <= 60`) and DOES evaluate against real per-patient lab values
  (e.g. one sampled patient at eGFR 24.8 fails the lower bound, another at
  eGFR 108.3 fails the upper bound) — unlike Trial A, eGFR is a live,
  varying signal here. Evaluated 200/200 sampled patients: still 100%
  `ineligible` overall, but across two distinct shapes (192x
  pass=3/fail=2/unknown=6, 8x pass=3/fail=3/unknown=5) rather than Trial
  A's single uniform shape.
- **Structural finding (not a bug, not fixed — out of scope per this
  task's "no matcher logic change" guardrail)**: Trial B's real eligibility
  text lists two alternative renal-function pathways as separate inclusion
  bullets — "eGFR 30-60" (criterion 89) and "eGFR >=60 with albuminuria"
  (criterion 90) — which ClinicalTrials.gov intends as OR'd alternatives.
  Our matcher ANDs every top-level criterion (`match_patient_db`: any
  single `fail` verdict anywhere makes the whole thing `ineligible`), so a
  patient must satisfy both mutually-exclusive eGFR ranges at once to ever
  reach `eligible` — practically unreachable. This is a *between-criteria*
  OR (two separate criterion rows), a different problem from the
  *within-criterion* OR the compound-criteria change already handles (one
  criterion's own sub-rules); fixing it would mean matcher logic changes
  explicitly out of scope here.
- **Field mapping**: re-confirmed `lab.egfr -> EGFR` bridging
  (`resolve_db_field` in `db_matching.py`) needed zero changes and is not
  the reason Trial A's eGFR stays unknown — verified by contrast: the
  identical bridging correctly resolves real values for Trial B's eGFR
  criteria on the same patients.
- **Verdict / hero trial**: neither trial produces any `eligible` patient
  in this dataset (both are 100% `ineligible` in the samples evaluated) —
  an honest result of low T2DM prevalence in the seed data plus, for Trial
  B, the OR-across-criteria structural gap above. **Trial B is the
  recommended hero for the demo screen**: it has more structured criteria
  (5 vs 3), and critically its eGFR criterion shows real, varying
  pass/fail against actual lab values with cited `source_lab_result_id`s,
  giving the per-criterion detail view genuine substance to point at.
  Trial A's eGFR criterion never resolves to a rule at all, so its
  candidate screen has one less real signal to show. Both trials remain
  importable/matchable side by side; nothing about Trial A was removed.
- Full regression pass after the imports: `/health` still `{status: ok}`,
  and the pre-existing `NCT04280705` trial still matches normally against
  the DB (1000 total patients, 10/10 sampled evaluated, no errors) —
  confirming the two new trial rows didn't disturb the existing pipeline.

---

## OR-of-AND rule groups + contains-operator fix (2026-08-16) — DONE

Follow-up to the hero-trials evaluation above. That evaluation surfaced two
real, separate issues; both are fixed here, additively, with full
regression coverage.

- **Cross-criteria OR (the structural gap flagged in the hero-trials
  write-up)**: Trial B's real text nests two alternative eGFR pathways
  under one shared parent bullet ("Evidence of DKD Stages 1-3:"), which
  the importer previously flattened into two independent top-level
  criteria -- and since the matcher ANDs every top-level criterion, no
  patient could ever satisfy both mutually-exclusive eGFR ranges at once.
  Fixed with a new, additive `rule_groups` field on `Criterion`: a list of
  AND-groups that are OR'd together (satisfying any ONE group is enough).
  `rules` (flat AND, unchanged) and the legacy top-level field/operator/
  value both still work exactly as before -- `rule_groups` is purely
  additive, and a single AND-group is the degenerate case of the new OR
  combination, verified byte-identical to the pre-change output for every
  existing criterion. Encoded into the same `trial_criteria.value` TEXT
  column as `{"rule_groups": [[...], ...]}` (a JSON *object*, vs. the
  existing flat-array encoding for `rules`) -- no DB schema change. The
  LLM prompt (`llm.py`) now recognizes both the "parent bullet + child
  bullets" pattern and inline "X, and/or Y" phrasing, with two new worked
  examples (including one that combines `rule_groups` with a still-true
  `needs_review` for a separate uncaptured qualifier, e.g. Trial A's
  "with evidence of chronicity" clause). Re-parsing Trial B now correctly
  merges what were criteria 89+90 into one 10-criterion trial (was 11);
  re-parsing Trial A structures its previously-fully-unstructured CKD/eGFR
  criterion into `rule_groups` too, though it stays needs_review (its own
  chronicity qualifier is separately uncaptured, correctly).
- **`contains`/`not_contains` operator bug (found while verifying the fix
  above, unrelated to it)**: `_evaluate_condition` in `matching.py` was
  comparing with `==` instead of substring containment -- so "contains
  type 2 diabetes" only matched a diagnosis label that was *exactly*
  "type 2 diabetes", never realistic clinical text like "Type 2 diabetes
  mellitus without complications". This silently made every diagnosis-
  or medication-based inclusion criterion across the *entire project*
  (both hero trials, and any future trial) fail for every real patient,
  no matter their actual diagnosis -- explains why every candidate screen
  all session showed a uniform diagnosis-fail rather than the T2DM cohort
  (106/1000 patients) actually passing. Fixed to real substring matching
  (`needle in haystack`), matching the field's documented intent and the
  LLM prompt's own description of the operator. `db_matching.py` shares
  the same `_evaluate_condition` function, so one fix covers both the
  in-memory and Supabase matchers.
- **Verification**: direct Kleene-logic unit tests for all 5 OR-group
  truth-table cases (known-true/known-false/unknown in every combination)
  before touching the server; DB encode/decode round-trip tests for
  `rule_groups` vs. the legacy flat-array and single-scalar encodings;
  live regression against the pre-existing `NCT04280705` trial (byte-
  identical verdicts/reasons before and after both changes); live
  end-to-end test of a `rule_groups` criterion through the real Phase 1
  `/match` HTTP endpoint; and, most concretely, matched a real patient
  with eGFR 19.97 mL/min/1.73m2 and a real "Type 2 diabetes mellitus
  without complications" diagnosis against Trial B post-fix and confirmed
  the full honest chain: age pass, diagnosis pass (previously always
  failed), eGFR **fail** with a legible "Path 1: ... OR Path 2: ..."
  explanation citing the real lab value, remaining unstructured criteria
  correctly unknown, overall `ineligible` -- genuinely traceable, not
  uniform. Aggregate impact on a 50-patient sample: both hero trials moved
  from 100% `ineligible` (every session before this fix) to a real mix of
  `ineligible` / `needs more data`, with Trial B showing 3 distinct
  verdict-count shapes (vs. Trial A's 2) and the only trial where the
  eGFR criterion can reach a definitive `fail` (Trial A's DKD criterion is
  a one-sided `eGFR<60` with no upper bound, so by the trial's own written
  logic it can never be definitively disqualified by eGFR alone) --
  reinforcing Trial B as the stronger hero trial.
- Full regression pass after both changes: `/health`, `/patients`,
  `/trials/{nct}/progress`, `/trials/{nct}/enrollment`, `/trials/{nct}/
  audit`, `/audit-log`, `/flagged-for-review` all still 200; the
  pre-existing `NCT04280705` trial's candidate screen unchanged.
- Known limitation, unchanged: `trial_criteria` still has no `reason`
  column, so a `rule_groups` criterion's needs_review explanation (e.g.
  "the chronicity qualifier isn't captured") lives in the live parse
  response but isn't persisted to the DB -- same pre-existing gap noted
  under the compound-criteria step.

## Patient-side consent screen: steps, withdraw, own progress (2026-08-16) — DONE

User feedback: the patient-facing `consent.html` was "very simple and not
intuitive" compared to the researcher dashboard. Pure front-end change,
built entirely on existing backend endpoints -- no new routes, no schema
changes.

- **Step indicator**: a visual Invited -> Consented -> Enrolled stepper at
  the top of every state (Declined/Withdrawn show a status pill instead),
  so patients can see where they are in the process instead of a single
  flat status line.
- **Real withdraw action -- was a genuine gap, not just polish**: the
  consent terms text has always promised "you may withdraw... at any time
  afterward without giving a reason," but no patient-facing control ever
  existed to do it -- `/trials/{nct}/patients/{pid}/withdraw` was wired up
  for the researcher dashboard only. Added a withdraw link (consented and
  enrolled states) with an inline, non-native confirm step (styled to
  match the page, not a browser `confirm()` dialog) before calling the
  existing endpoint. Verified live: confirm/cancel both work, a real
  withdraw persists and survives reload, and the audit log records it
  correctly (`patient.withdrawn`, from_status `consented`).
- **Enrolled patients now see their own progress**: reuses the existing
  `/trials/{nct}/progress` endpoint (already built for the researcher
  dashboard), filtered client-side to the signed-in patient's own
  `patient_id` -- baseline date plus a per-test baseline-vs-latest row
  with an honest trend label (Improved / Worsened / No clear trend / No
  follow-up data yet). Verified live against a real enrolled patient: real
  baseline/latest values, a genuine "Worsened" eGFR trend rendered
  correctly.
- **Richer initial invitation**: shows the trial's `primary_endpoint`
  ("This trial is studying: ...") when available, so patients see what
  the trial is actually for before deciding, not just its NCT ID and
  phase.
- **Known, un-fixed limitation**: `withdraw_patient()` in `enrollment.py`
  hardcodes `actor="researcher"` in its audit row regardless of caller,
  since one endpoint now serves both the researcher dashboard and this
  patient screen. A patient-initiated withdrawal is logged with the wrong
  actor. Not fixed here -- correctly attributing it would mean either
  trusting a client-supplied actor value (a spoofing concern worth
  discussing, not deciding unilaterally) or splitting into a separate
  patient-facing endpoint; left as a known gap rather than a quick patch.
- Verified live in a real browser across every state: invited (with the
  new primary_endpoint line), consented, enrolled (with real progress
  data), declined, withdrawn (both the fresh transition and reload-then-
  already-withdrawn), and no-invitation/error paths -- all render
  correctly with zero backend changes required.

## Population-friendly hero trial: TM-METABOLIC-001 (2026-08-16) — DONE

Goal: a candidate screen that's mostly `eligible`, honestly. Diagnose-first,
then pick a trial our population genuinely passes -- no matcher changes to
force it.

**Part 1 -- diagnosed NCT07348718's low-eligible mix. No bug found.**
Per-criterion tally across 200 patients: age (pass=200), T2DM-required
diagnosis inclusion (pass=31, fail=169), eGFR/UACR DKD criterion (unknown=200
for all), T1DM exclusion (pass=200). The eGFR criterion's `unknown` is NOT a
field-mapping bug -- hand-verified the real eGFR value resolves correctly
(e.g. 64.1989, cited) every time; it's `unknown` because the real criterion
text is "eGFR<60 and/or UACR>=30" and we have zero UACR data, so the OR can
never be ruled out for the ~89% of patients with eGFR>=60. Hand-checked the
T1DM exclusion against a real T2DM patient (verdict `pass`, correctly not
excluded) and confirmed zero patients in the dataset even have a T1DM
diagnosis, so the exclusion is untestable-but-correctly-inert, not broken.
Bottleneck is legitimate: only 106/1000 patients have T2DM, and the trial
requires it. Nothing fixed here because nothing was broken.

**Part 2, Option 1 -- NCT04589351 control-group arm. Tried it, real
findings, real bug found (not in Part 1's trial).** The full eligibility
text mixes four distinct cohort definitions (intervention arm, control
group, two more) with contradictory criteria (e.g. one arm requires T2D,
another excludes it) -- feeding the whole document to the parser would
silently conflate cohorts, so only the "control group" section (age 40-75,
eGFR>=60, plus its own exclusions) was excerpted and parsed on its own.
- **Found a real, generalizable bug while verifying this**: 4 of the 16
  parsed criteria expressed an exclusion using `not_contains` (e.g. "not
  contains diabetes mellitus") combined via AND/single-rule -- semantically
  the opposite of our established convention (state the *disqualifying*
  condition via `contains`; the matcher's exclusion-inversion handles the
  rest). Combined with correct, unchanged matcher logic, this inverted the
  result: a real test showed a patient with only hypertension (no diabetes/
  CKD/heart disease/COPD) scored `fail` (wrongly excluded), while a genuine
  T2DM patient scored `pass` (wrongly included) -- exactly backwards.
  **The matcher was not touched.** Fixed by correcting the stored criteria
  rows to the existing, already-tested pattern: `rule_groups` of
  `contains`-phrased alternatives for the multi-disease criterion, and
  `contains` (not `not_contains`) for the three single-condition ones.
  Re-verified against the same two patients: hypertension-only patient no
  longer wrongly excluded; T2DM patient now correctly `fail`s. This is a
  real LLM-prompt-phrasing gap (parallel to the earlier SpO2 OR-extraction
  bug), generalizable beyond this one trial -- flagged here, not fixed at
  the prompt level, since that's a bigger change out of scope for this task.
- **Even fully fixed, this real trial cannot produce `eligible` verdicts,
  and that's inherent to using real trial text, not a bug.** Matched 150
  patients before and after the fix: before, 150/150 `ineligible` (the
  inversion bug was wrongly excluding everyone); after, 40 `needs more
  data` / 110 `ineligible`, zero `eligible`. Ten of its sixteen criteria
  (informed consent capacity, investigator judgment, "no other study
  participation," MR-scanner contraindications, compliance, etc.) are
  either fully unstructurable or partially-structured-but-`needs_review`
  (so a would-be pass is honestly downgraded to `unknown`, never
  overclaimed) -- every real ClinicalTrials.gov trial has soft criteria
  like these, so no real trial will ever clear the `needs_review` bar on
  every criterion. This is reported as the honest answer for Option 1, not
  worked around.

**Part 2, Option 2 -- custom trial `TM-METABOLIC-001` ("Metabolic Health
Screening Cohort"), chosen as the hero.** Four fully-structurable criteria,
each grounded in real coverage (age, eGFR, HbA1c universal; ALT universal):
inclusion age>=18, inclusion eGFR>=30 mL/min/1.73m2, inclusion HbA1c<=7.5%,
exclusion ALT>80 U/L. No `needs_review` criteria at all, so nothing is ever
capped at `unknown` for lack of structure -- only for lack of *data*, and
all four fields are universal (1000/1000), so there's no lack-of-data case
either. A fifth criterion (total cholesterol<=240, ~189/1000 coverage) was
tried specifically to demonstrate the honest-unknown ("flag, don't guess")
behavior as suggested, but was dropped from the hero trial after testing:
with only ~19% coverage, it capped 78% of patients at `needs more data`
rather than being a minor contrast note, directly conflicting with the
"mostly eligible" goal. Noted here, not silently discarded -- can be added
back (or tested standalone) if the honest-unknown demonstration is wanted
for a different part of the demo.
- Since this trial has no ClinicalTrials.gov record, `GET /trials/{nct_id}`
  needed a small additive fallback (`main.py`): try the live
  ClinicalTrials.gov lookup exactly as before; only on a 404 does it fall
  back to reading the trial's own `trials` table row. Every real NCT ID
  still resolves via the live API exactly as before (never reaches the
  fallback branch) -- verified NCT04280705 still returns 200 via the live
  path after this change.

**Part 3 -- match results.** 100 patients evaluated (coarse filter matched
all 1000, since eGFR/age aren't narrowed by the current coarse-filter
fields): **94 eligible, 6 ineligible, 0 unknown.** Sample eligible patient
(00796351): age 69 pass, eGFR 108.335 pass (cites `lab_result_id` 2882),
HbA1c 4.9885 pass, ALT 18.0092 pass -- overall `eligible`. Sample ineligible
patient (003be4ad): eGFR 24.7894 real **fail** against the >=30 threshold
(cites `lab_result_id` 2358), everything else passes -- overall
`ineligible`. Every verdict is a real comparison against a real patient
value with a real citation; nothing fabricated.

**Part 4 -- regression.** `/health`, `/patients`, `/consent.html`,
`/researcher.html`, `/audit-log`, `/flagged-for-review` all still 200;
NCT04280705's candidate screen byte-identical to before (1 pass/1 fail/11
unknown pattern unchanged); both existing hero trials (NCT07348718,
NCT04791358) still match successfully. Every prior trial kept, none
deleted. Only code change: the additive `get_trial` fallback in `main.py`.

## TM-METABOLIC-001 demo enrollment + trial-health seeding (2026-08-16) — DONE

Pure data enrichment for the hero trial's demo, using only existing
endpoints/functions -- no code changes.

- 7 eligible candidates taken through the real state machine (invite ->
  consent -> enroll via `enrollment.py`'s existing functions), so
  `baseline_date` is genuinely set by the same `enroll_patient()` path a
  researcher action uses -- not backdated or special-cased.
- For each, added one follow-up `lab_results` row per test the hero trial
  actually reads (eGFR, HbA1c, ALT) dated 3-10 weeks after their own
  baseline_date, with a modest (~4%) random perturbation *from that
  patient's own baseline value* -- not resampled from the population like
  `scripts/seed_extra_labs.py` does, since a real follow-up reading should
  track the same person, not a fresh random patient. AST/creatinine/HGB
  were deliberately left unseeded for these patients, so their progress
  view honestly shows `no_data` rather than a fabricated trend.
- Result, read live from `/trials/TM-METABOLIC-001/progress`: 7 enrolled,
  7 active, 0 dropouts, 43% improved on the auto-selected primary test
  (ALT) -- a genuine mixed outcome (some patients improved, some
  worsened, per test), not curated to look good. Verified live in the
  researcher dashboard's Trial Progress tab.
- **Follow-up (same day): raised the success rate above 60% on request**,
  for a stronger demo number. None of the 3 tracked tests cleared 60%
  with the original 7 patients (ALT 43%, eGFR 57%, HbA1c 43%), and which
  test gets auto-selected as primary isn't caller-controlled (ties broken
  by dict-iteration order), so the fix needed to push all three past 60%,
  not just one. Enrolled 8 more eligible candidates (verified `eligible`
  via `/match` before enrolling each) via the same real state-machine
  path, with follow-up labs generated the same way as before but with an
  intentional favorable mean-shift (~3-6% toward the "improved" direction
  per `TEST_DIRECTION`, plus smaller random noise so it isn't a uniform
  rubber-stamp -- the first batch of 5 still produced 2 real worsened
  outcomes despite the bias). This is authoring our own synthetic
  follow-up values with a chosen distribution, not altering a verdict or
  re-writing an existing result -- the original 7 patients' rows were
  untouched. Final result: 15 enrolled, 15 active, ALT 66.7%, eGFR 73.3%,
  HbA1c 66.7% improved -- all three comfortably above 60%, so the number
  holds regardless of which test the dashboard ends up showing.
- No git-trackable change -- this is Supabase row data (`patient_trial`,
  `lab_results`, `audit_log`), same category as the hero-trial criteria
  import earlier. Logged here for the record, same as that was.

## Demo narrative (what to show judges)
1. Paste a real trial's messy eligibility text → watch it become clean rules.
2. Show a ranked list of patients for that trial.
3. Expand one patient → every criterion with pass/fail/unknown + why.
4. Point out an `unknown` and explain: the system flags missing data instead
   of guessing — the coordinator decides. That's the compliance story.
5. (Phase 2) Point at `match_results.source_lab_result_id` and explain: every
   verdict cites the exact lab row that justified it, not just a reason
   string — that's source data verification, not just an audit note.
6. Show `NCT04280705`'s "Male or non-pregnant female adult >= 18" criterion:
   the age part is a real, evaluated rule (different verdict per patient);
   the sex/pregnancy OR part stays honestly flagged instead of guessed or
   thrown away entirely — this is the "partial credit, never overreach"
   story in one criterion.

## Guardrails / notes to self
- Commit after every working step.
- Turn OFF auto-accept edits before touching the matching engine so you
  review changes to core logic.
- All patient data is synthetic — no real PHI, ever.
- Keep the LLM scoped to extraction only; matching stays deterministic
  Python. Don't let a second LLM provider creep in for something Gemini
  already does (parsing).
