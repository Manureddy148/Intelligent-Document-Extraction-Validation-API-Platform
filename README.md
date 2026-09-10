# Intelligent Document Extraction & Validation API Platform

A FastAPI service that extracts structured fields from uploaded documents
(PDFs, images, or plain text) and validates them against per-document-type
rules — a starting point for building automated document-processing
pipelines (invoices, receipts, ID cards, etc).

## Features

- **Extraction** — pulls raw text from PDFs (`pdfplumber`) and images
  (`pytesseract` OCR), then runs heuristic field extraction (dates,
  amounts, emails, invoice numbers, ID numbers) via regex.
- **Validation** — checks extracted fields against required-field and
  format rules defined per `DocumentType` (invoice, receipt, id_card,
  generic).
- **Combined pipeline** — a single `/process` endpoint runs extraction and
  validation in one call.

## Project layout

```
app/
  main.py                  # FastAPI app + router wiring
  core/config.py           # Settings (env-driven)
  models/schemas.py        # Pydantic request/response models
  services/extraction.py   # Text extraction + heuristic field parsing
  services/validation.py   # Rule-based field validation
  api/routes/health.py     # GET /health
  api/routes/documents.py  # /documents/extract, /validate, /process
tests/                     # pytest suite (uses FastAPI TestClient)
```

## Setup

Requires Python 3.10+. On Debian/Ubuntu, image OCR also needs the
`tesseract-ocr` system package (already installed in the provided
Dockerfile).

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
uvicorn app.main:app --reload
```

The API docs are then available at `http://localhost:8000/docs`.

## Running with Docker

```bash
cp .env.example .env
docker compose up --build
```

## API

| Method | Path                        | Description                                   |
|--------|-----------------------------|------------------------------------------------|
| GET    | `/health`                   | Liveness check                                 |
| POST   | `/api/v1/documents/extract` | Upload a file, get back raw text + fields      |
| POST   | `/api/v1/documents/validate`| Validate a `document_type` + field dict        |
| POST   | `/api/v1/documents/process` | Upload + extract + validate in one call        |

`extract` and `process` take a multipart form with `file` and
`document_type` (`invoice`, `receipt`, `id_card`, or `generic`).

## Tests

```bash
pytest
```
