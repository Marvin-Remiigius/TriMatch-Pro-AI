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

## Dashboard

Open `http://127.0.0.1:8000/` (`static/index.html`), enter an NCT id (e.g.
`NCT04280705`), and click "Load candidates". This calls
`/trials/{nct_id}/candidates` and renders the trial plus ranked patient
cards; click a card to expand its full per-criterion breakdown.

## Endpoints

- `GET /health` — returns `{"status": "ok"}`
- `GET /trials/{nct_id}` — fetches a trial from ClinicalTrials.gov (title,
  phase, status, primary endpoint, raw eligibility criteria)
- `POST /trials/{nct_id}/import` — fetches a trial from ClinicalTrials.gov
  and upserts it into the Supabase `trials` table (`nct_id`, `title`,
  `phase`, `status`, `primary_endpoint`); returns the raw eligibility text
  in the response so you can see what's about to be parsed
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
