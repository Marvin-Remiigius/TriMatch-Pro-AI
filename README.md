# TriMatch Pro AI

Clinical Trial Matching & Research Assistant. Matches patients to clinical
trials by parsing trial eligibility criteria into structured rules and
evaluating them against patient records.

## Requirements

- Python 3.10+
- A Gemini API key (free tier) from [Google AI Studio](https://aistudio.google.com/apikey)
- A Supabase project (URL + anon key from Project Settings -> API), with
  `migrations/migration_trial_layer.sql` applied via the Supabase SQL editor

## Setup

```powershell
py -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in your values (`GEMINI_API_KEY`,
`DATABASE_URL` -- the Supabase project URL, not a `postgresql://` string --
and `SUPABASE_ANON_KEY`):

```powershell
copy .env.example .env
```

## Run

```powershell
uvicorn main:app --reload
```

The API will be available at `http://127.0.0.1:8000`, and the dashboard at
`http://127.0.0.1:8000/`.

## Database

`migrations/migration_trial_layer.sql` adds the trial/enrollment/compliance
layer (`trials`, `trial_criteria`, `patient_trial`, `match_results`,
`trial_metrics`, `audit_log`) on top of an existing Supabase schema that
already has 1000 synthetic `patients`, their `lab_results`, and `diagnoses`.
Paste it into the Supabase SQL editor to apply (idempotent, safe to re-run).
`db.py` holds the shared Supabase client (`SUPABASE_ANON_KEY`, so requests
are subject to whatever RLS policies are configured on each table). See
`PLAN.md` for how each table maps to the project's compliance/matching
requirements, and what's built so far vs. still open.

## Dashboards

- `http://127.0.0.1:8000/` (`static/index.html`) — Phase 1 fallback demo,
  in-memory 5-patient store. Enter an NCT id (e.g. `NCT04280705`) and click
  "Load candidates".
- `http://127.0.0.1:8000/researcher.html` — Phase 2 researcher dashboard,
  live against the real Supabase data (1000 patients). Enter an NCT id and
  click "Load trial"; click a ranked candidate to see its full per-criterion
  breakdown, including the source `lab_result` citation behind every
  lab-based verdict. Trials without a clean age/diagnosis criterion (like
  `NCT04280705`) fall back to matching the full patient pool and can take
  up to ~30-60s to load — this is a known scale limitation, not a bug (see
  `PLAN.md` step 5). The **Trial progress** tab shows enrolled/active/
  dropout counts, a transparently-defined `success_rate`, and a
  baseline-vs-latest lab readout per enrolled patient with source
  citations — currently only populated for `NCT04280705` (see
  `scripts/seed_enrollment.py`; run it against another trial's NCT id to
  demo progress there too). The **Enrollment** tab is the real invite ->
  consent -> enroll pipeline: each ranked candidate shows its current
  status and the one next action available, plus the trial's audit trail
  rendered live on the same screen. "Invite" opens
  `/consent.html?patient=...&trial=...` in a new tab — the one
  patient-facing screen (Accept/Decline; no login, no portal).

## Endpoints

- `GET /health` — returns `{"status": "ok"}`
- `GET /trials/{nct_id}` — fetches a trial from ClinicalTrials.gov (title,
  phase, status, primary endpoint, raw eligibility criteria)
- `POST /trials/{nct_id}/import` — fetches a trial from ClinicalTrials.gov
  and upserts it into the Supabase `trials` table (`nct_id`, `title`,
  `phase`, `status`, `primary_endpoint`); returns the raw eligibility text
  in the response so you can see what's about to be parsed
- `POST /trials/{nct_id}/parse-criteria` — parses the trial's eligibility
  text with the same Gemini parser as `/parse-criteria` below, and replaces
  (delete-then-insert) that trial's `trial_criteria` rows in Supabase
- `POST /trials/{nct_id}/match/{patient_id}` — deterministically matches
  one real Supabase patient against the trial's parsed criteria, writes
  `match_results` (citing the exact `lab_results` row behind any lab-based
  verdict), and returns the same shape as `/match` below
- `GET /trials/{nct_id}/db-candidates` — coarse-filters the 1000-patient
  pool via indexed SQL (age range, required diagnosis) where possible, then
  fully matches and ranks the survivors. Query params: `limit` (returned
  list size, default 50), `max_evaluate` (cap on how many survivors get
  fully matched, default 200)
- `GET /trials/{nct_id}/progress` — for every patient enrolled
  (`patient_trial.status` in `enrolled`/`withdrawn`) in the trial, computes
  baseline (nearest lab reading on/before `baseline_date`) vs. latest (most
  recent reading) per test, with both source `lab_result_id`s, a deviation,
  and a status of `improved`/`worsened`/`indeterminate`/`no_data` (direction
  is only ever looked up per test, never guessed). Also returns trial-level
  `enrolled`/`active`/`dropouts`/`success_rate` and which test_code
  `success_rate` was computed against (`primary_test_code_used`, either
  passed via `?primary_test_code=` or auto-selected by data coverage —
  never inferred from the trial's free-text primary endpoint). Always
  computed live. Optional query param: `primary_test_code`.
- `POST /trials/{nct_id}/compute-metrics` — same computation as `/progress`,
  additionally upserts the headline (enrolled/active/dropouts/success_rate)
  into the `trial_metrics` table.
- `POST /trials/{nct_id}/patients/{patient_id}/invite` — creates a
  `patient_trial` row at `status='invited'`. 409s if one already exists.
- `POST /trials/{nct_id}/patients/{patient_id}/consent` — only from
  `invited`; records terms-shown-then-accepted-then-consented as two
  audit-logged transitions, ending at `status='consented'`. 409s otherwise.
- `POST /trials/{nct_id}/patients/{patient_id}/enroll` — only from
  `consented`; sets `status='enrolled'`, `enrolled_at`, and
  `baseline_date` together (the anchor `/progress` depends on). 409s
  otherwise.
- `POST /trials/{nct_id}/patients/{patient_id}/withdraw` — from any active
  state (`invited`/`accepted`/`consented`/`enrolled`) to `withdrawn`.
- `POST /trials/{nct_id}/patients/{patient_id}/decline` — from
  `invited`/`accepted` to `declined`.
- `GET /trials/{nct_id}/enrollment` — every `patient_trial` row for the
  trial (current status + timestamps).
- `GET /trials/{nct_id}/audit` — the `audit_log` rows for the trial
  (actor/action/entity_id/detail), newest first. Optional `limit`.
- `GET /patients` — lists synthetic patients
- `GET /patients/{id}` — fetches one synthetic patient
- `POST /parse-criteria` — `{"text": "<raw eligibility text>"}`, uses Gemini
  to extract structured rules; criteria that can't be reduced to a single
  field/operator/value are flagged `needs_review` instead of guessed
- `POST /match` — `{"patient_id": "P001", "criteria": [...], "nct_id": "..."}`
  (criteria from `/parse-criteria`; `nct_id` optional, for the audit trail),
  deterministically evaluates each criterion against the patient
  (`pass`/`fail`/`unknown` + source field + reason) and returns an overall
  verdict of `eligible`, `ineligible`, or `needs more data`. Every criterion
  decision is logged.
- `GET /trials/{nct_id}/candidates` — fetches the trial, parses its
  eligibility criteria once, matches every synthetic patient against them,
  and returns the parsed criteria plus patients ranked best-candidate-first.
  Every criterion decision is logged.
- `GET /audit-log` — every logged match decision (timestamp, trial, patient,
  criterion, source field, verdict, reason), newest first. Optional query
  params: `nct_id`, `patient_id`, `limit`.
- `GET /flagged-for-review` — same as `/audit-log` but restricted to
  `unknown` verdicts (criteria that couldn't be structured, or where the
  patient is missing the needed data) — the compliance/human-review queue.
