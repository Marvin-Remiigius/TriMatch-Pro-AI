# TriMatch Pro AI

A minimal FastAPI service.

## Requirements

- Python 3.10+

## Setup

```powershell
py -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run

```powershell
uvicorn main:app --reload
```

The API will be available at `http://127.0.0.1:8000`.

## Endpoints

- `GET /health` — returns `{"status": "ok"}`
