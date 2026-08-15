# TriMatch Pro AI

Clinical Trial Matching & Research Assistant. Matches patients to clinical
trials by parsing trial eligibility criteria into structured rules and
evaluating them against patient records.

## Requirements

- Python 3.10+
- A Gemini API key (free tier) from [Google AI Studio](https://aistudio.google.com/apikey)

## Setup

```powershell
py -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in your key:

```powershell
copy .env.example .env
```

```
GEMINI_API_KEY=your-key-here
```

## Run

```powershell
uvicorn main:app --reload
```

The API will be available at `http://127.0.0.1:8000`, and the dashboard at
`http://127.0.0.1:8000/`.

## Dashboard

Open `http://127.0.0.1:8000/` (`static/index.html`), enter an NCT id (e.g.
`NCT04280705`), and click "Load candidates". This calls
`/trials/{nct_id}/candidates` and renders the trial plus ranked patient
cards; click a card to expand its full per-criterion breakdown.

## Endpoints

- `GET /health` — returns `{"status": "ok"}`
- `GET /trials/{nct_id}` — fetches a trial from ClinicalTrials.gov (title,
  phase, status, raw eligibility criteria)
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
