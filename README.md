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

The API will be available at `http://127.0.0.1:8000`.

## Endpoints

- `GET /health` — returns `{"status": "ok"}`
- `GET /trials/{nct_id}` — fetches a trial from ClinicalTrials.gov (title,
  phase, status, raw eligibility criteria)
- `GET /patients` — lists synthetic patients
- `GET /patients/{id}` — fetches one synthetic patient
- `POST /parse-criteria` — `{"text": "<raw eligibility text>"}`, uses Gemini
  to extract structured rules; criteria that can't be reduced to a single
  field/operator/value are flagged `needs_review` instead of guessed
- `POST /match` — `{"patient_id": "P001", "criteria": [...]}` (criteria from
  `/parse-criteria`), deterministically evaluates each criterion against the
  patient (`pass`/`fail`/`unknown` + reason) and returns an overall verdict
  of `eligible`, `ineligible`, or `needs more data`
- `GET /trials/{nct_id}/candidates` — fetches the trial, parses its
  eligibility criteria once, matches every synthetic patient against them,
  and returns the parsed criteria plus patients ranked best-candidate-first
