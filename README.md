# Intelligent Document Extraction, Validation & API Platform

An end-to-end document intelligence service. It accepts invoices, balance
sheets, profit & loss accounts and cash flow statements as PDF / JPG / PNG,
validates the upload, extracts the fields and tables the document actually
contains, re-computes the financial relationships that should hold, stores the
result, and serves everything through a REST API and a dashboard.

![Architecture](docs/architecture.png)

The solution presentation is in [`docs/solution_presentation.pdf`](docs/solution_presentation.pdf)
(also available as `.pptx`), and both it and the diagram above are regenerated
by the scripts alongside them.

## Repository layout

```
backend/       FastAPI application, services, repositories, tests
frontend/      Jinja2 templates and static CSS/JS for the dashboard
docs/          architecture diagram, solution presentation, screenshots
sample_outputs/  real API responses for every required scenario
case_study/    the original brief and the provided sample documents
```

## Deployment URLs

| Item | URL |
| --- | --- |
| Frontend (dashboard) | _add after deploying — same host as the API, path `/`_ |
| Backend API base | _add after deploying_ |
| Swagger / OpenAPI | _API base_ + `/docs` |
| Health endpoint | _API base_ + `/api/v1/health` |
| GitHub repository | https://github.com/Manureddy148/Intelligent-Document-Extraction-Validation-API-Platform |

> The frontend is served by the same FastAPI application as the API, so one
> deployment covers both. See [Deployment](#deployment) for the steps — it needs
> a hosting account, so the URLs above must be filled in after you deploy.

## Contents

- [Solution overview](#solution-overview)
- [Technology choices](#technology-choices)
- [Local setup](#local-setup)
- [Environment variables](#environment-variables)
- [API](#api)
- [OCR and extraction approach](#ocr-and-extraction-approach)
- [Financial validation rules and tolerance](#financial-validation-rules-and-tolerance)
- [Persistence](#persistence)
- [Deployment](#deployment)
- [Testing](#testing)
- [Measured accuracy](#measured-accuracy-on-the-provided-dataset)
- [Known limitations](#known-limitations)
- [What I would change for production](#what-i-would-change-for-production)
- [AI assistance declaration](#ai-assistance-declaration)

## Solution overview

A single upload flows through five stages, orchestrated by
`document_service.py`:

1. **File validation** (`document_validation_service.py`) — the file type is
   determined from its magic bytes rather than the declared content type, so a
   renamed `.exe` is rejected. Empty files, unreadable/encrypted PDFs and
   documents over the 3-page limit fail with a typed error.
2. **Text + geometry extraction** (`ocr_service.py`) — a PDF with a text layer
   is read directly with `pdfplumber`; otherwise the page is rasterised and run
   through Tesseract. Both paths return words **with bounding boxes**.
3. **Structured extraction** (`extraction_service.py`, `utils/layout.py`) —
   rows are rebuilt from word geometry and each number is assigned to a
   reporting-period column.
4. **Optional LLM recovery** (`llm_extraction_service.py`) — only fills fields
   left null; skipped entirely when no API key is set.
5. **Financial validation** (`financial_validation_service.py`) — recomputes
   the relationships for the document type and reports formula, operands,
   calculated vs reported value, variance and status.

The result is persisted and returned as JSON.

### Processing status

`processing_status` describes whether the **document could be processed**:
`PASS` when meaningful fields were extracted, `FAILED` when the document was
readable but nothing usable came out of it (for example a scan too poor to
recover any key field). Uploads rejected at stage 1 never reach this state —
they return an HTTP error instead.

A financial check that does not reconcile is a *business* outcome, not a
processing failure, so it is reported separately under `validation.overall_status`.
A document can therefore be `processing_status: PASS` with
`validation.overall_status: FAIL` — see
[`sample_outputs/06_profit_and_loss_2025_validation_failure.json`](sample_outputs/06_profit_and_loss_2025_validation_failure.json).

## Technology choices

| Choice | Why |
| --- | --- |
| **FastAPI** | Generates the mandatory Swagger/OpenAPI docs from the code, has first-class multipart upload support, and Pydantic gives typed request/response models for free. |
| **Tesseract** (via `pytesseract`) | Free and self-hosted, so there is no per-page cost, no API key to leak, and no third-party quota to run out of during evaluation. |
| **pdfplumber / pdf2image + Poppler** | `pdfplumber` reads native PDF text *and* word coordinates; the sample statements have no text layer, so Poppler rasterises them for OCR. |
| **SQLAlchemy + SQLite (Postgres in deployment)** | SQLite needs no service to run locally; the same ORM code points at managed Postgres in deployment by changing one environment variable. |
| **Jinja2 + vanilla JS frontend** | The case study asks for HTML/CSS served alongside the Python API; a build step and an SPA framework would add tooling without adding capability. |
| **Rule-based extraction, LLM optional** | The deployed demo works with no API key and produces the same output every run; the LLM path is available where OCR genuinely cannot recover a field. |

## Local setup

Requires **Python 3.10+** and two system packages.

```bash
# System dependencies (Debian/Ubuntu)
sudo apt-get install -y tesseract-ocr poppler-utils
# macOS: brew install tesseract poppler

git clone https://github.com/Manureddy148/Intelligent-Document-Extraction-Validation-API-Platform.git
cd Intelligent-Document-Extraction-Validation-API-Platform

python -m venv backend/.venv
source backend/.venv/bin/activate
pip install -r backend/requirements-dev.txt

cp .env.example backend/.env      # optional; every value has a default

cd backend
uvicorn app.main:app --reload
```

- Dashboard: <http://localhost:8000/>
- Swagger: <http://localhost:8000/docs>
- Health: <http://localhost:8000/api/v1/health>

With Docker instead (system packages already included):

```bash
docker compose up --build       # serves on http://localhost:8000
```

## Environment variables

All settings are read from the environment with the `IDEV_` prefix; nothing is
hardcoded and no secret is committed. Full list in [`.env.example`](.env.example).

| Variable | Default | Purpose |
| --- | --- | --- |
| `IDEV_DATABASE_URL` | `sqlite:///./data/documents.db` | Any SQLAlchemy URL. A managed `postgres://` URL is normalised automatically. |
| `IDEV_MAX_PAGES` | `3` | Page limit enforced before OCR. |
| `IDEV_MAX_UPLOAD_SIZE_MB` | `15` | Upload ceiling. |
| `IDEV_OCR_DPI` | `200` | Rasterisation DPI (see [measured accuracy](#measured-accuracy-on-the-provided-dataset)). |
| `IDEV_OCR_PSM` | `3` | Tesseract page-segmentation mode. |
| `IDEV_FINANCIAL_TOLERANCE_ABSOLUTE` | `1.0` | Absolute arm of the tolerance. |
| `IDEV_FINANCIAL_TOLERANCE_RELATIVE` | `0.01` | Relative arm of the tolerance. |
| `IDEV_LLM_API_KEY` | unset | Enables the optional LLM recovery pass. |
| `IDEV_LLM_MODEL` | `claude-opus-5` | Model used by that pass. |

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/documents/process` | Upload and process a document. |
| `GET` | `/api/v1/documents` | List processed documents (dashboard). |
| `GET` | `/api/v1/documents/{document_name}` | Latest stored result for that name. |
| `GET` | `/api/v1/health` | Health check. |

### Process a document

```bash
curl -X POST "$API/api/v1/documents/process" \
  -F "file=@balance_sheet.pdf;type=application/pdf" \
  -F "document_type=balance_sheet"
```

`document_type` is one of `invoice`, `balance_sheet`, `profit_and_loss`,
`cash_flow_statement`.

```jsonc
{
  "document_name": "Consolidated Balance Sheet 2017.pdf",
  "document_type": "balance_sheet",
  "processing_status": "PASS",
  "file_validation": { "file_type": "application/pdf", "is_supported": true,
                       "is_readable": true, "page_count": 1, "status": "PASS" },
  "extracted_data": {
    "statement_periods": ["31-Mar-17", "31-Mar-16"],
    "total_assets": {
      "value": { "31-Mar-17": 8923441607.0, "31-Mar-16": 7622123264.0 },
      "page_number": 1,
      "source_text": "Total 8,923,441,607 7,622,123,264"
    },
    "total_equity": { "value": null, "page_number": null, "source_text": null,
                      "note": "Not disclosed as a single line in this statement format..." },
    "line_items": { "capital_and_liabilities": [ /* every row, per period */ ], "assets": [ /* ... */ ] }
  },
  "validation": {
    "checks": [{
      "name": "balance_sheet_equality[31-Mar-17]",
      "formula": "Total Capital & Liabilities ≈ Total Assets",
      "operands": { "total_capital_and_liabilities": 8923441607.0, "total_assets": 8923441607.0 },
      "calculated_value": 8923441607.0, "reported_value": 8923441607.0,
      "variance": 0.0, "status": "PASS"
    }],
    "overall_status": "PASS",
    "issues": []
  },
  "processing_metadata": { "ocr_used": true, "ocr_engine": "tesseract",
                           "extraction_method": "rule_based_layout",
                           "processed_at": "2026-09-10T12:27:00Z",
                           "processing_time_ms": 2840, "pages_processed": 1 }
}
```

Every scalar field carries its `page_number` and the `source_text` row it came
from, so any value can be traced back to the document. Fields the document does
not contain are `null` — never guessed.

### Retrieve and list

```bash
curl "$API/api/v1/documents/Consolidated%20Balance%20Sheet%202017.pdf"
curl "$API/api/v1/documents?limit=20&offset=0"
```

Processing the same document name again replaces the stored result, so
get-by-name always returns the latest.

### Errors

Failures return the same envelope with an appropriate status code:

```json
{ "error": { "code": "UNSUPPORTED_FILE_TYPE", "message": "Only PDF / JPG / PNG documents are supported." } }
```

| Code | HTTP |
| --- | --- |
| `UNSUPPORTED_FILE_TYPE` | 415 |
| `EMPTY_FILE`, `CORRUPTED_FILE`, `PAGE_LIMIT_EXCEEDED` | 400 |
| `FILE_TOO_LARGE` | 413 |
| `DOCUMENT_NOT_FOUND` | 404 |
| `EXTRACTION_FAILED` | 422 |
| `INTERNAL_ERROR` | 500 |

Stack traces are never returned to the caller; they go to the application log.
Worked examples of every case are in [`sample_outputs/`](sample_outputs/).

## OCR and extraction approach

**OCR service:** free/self-hosted Tesseract, with Poppler rasterising PDFs that
have no text layer (all of the provided statements). No paid OCR service is
used, so there is no key to configure and no quota to exhaust.

**Why the extractor is layout-aware.** Reading OCR output line by line loses the
table structure, and two failure modes follow directly from that:

- A row populated for only one year (`Total    743,732,155`) gives no clue
  *which* year the number belongs to. Positionally it is the second column, but
  a text parser sees a single value and attributes it to the first period.
- Tesseract's default page segmentation frequently emits all the labels as one
  block and all the numbers as another, so labels and values are separated
  entirely in the text stream.

So the pipeline keeps the word bounding boxes from both `pdfplumber` and
Tesseract, and then:

1. groups words into rows by vertical overlap (re-uniting labels with values no
   matter what order OCR emitted them in);
2. finds the value columns by clustering the **right edges** of numeric tokens
   (financial tables are right-aligned), preferring the best-supported and
   rightmost clusters;
3. assigns each number to the nearest column, which puts a lone value in its own
   period and drops schedule/note reference numbers that sit outside the value
   columns;
4. walks rows in order tracking the current section (`CAPITAL AND LIABILITIES`,
   `ASSETS`, `INCOME`, …). A row carrying values is always a line item, never a
   heading — otherwise labels such as "Other income" get swallowed by the
   `INCOME` heading pattern.

Every labelled row is returned under `extracted_data.line_items`, not just the
named minimum fields, so nothing visible in the table is dropped.

**Optional LLM pass.** If `IDEV_LLM_API_KEY` is set, fields still null after the
deterministic pass are sent — with the OCR text — to the Claude Messages API
using a JSON-schema structured output. It is instructed to copy values verbatim
and return `null` rather than guess, it can only *fill* nulls (never overwrite a
grounded value), and any failure falls back silently to the deterministic
result. Values it supplies are marked `"extraction_method": "llm_assisted"`.

## Financial validation rules and tolerance

A check passes when `|calculated − reported|` is within
`max(1.0, 1% × max(|calculated|, |reported|))`. The relative arm matters because
the statements report figures in thousands/crore where a rounded last digit is
normal; the absolute arm keeps small invoice amounts sensible.

If any value a check needs is missing, the check is `NOT_APPLICABLE` — never a
failure. Summing a section whose rows were not all read would treat an unread
row as zero, so those reconciliations are also `NOT_APPLICABLE` rather than
reporting a discrepancy the document does not support.

| Document | Checks |
| --- | --- |
| **Invoice** | `quantity × unit_price ≈ line amount` per line item; `Σ line amounts ≈ subtotal` (or total); `subtotal + tax − discount ≈ total`; where tax is included in the printed total, the tax-inclusive variant is checked instead; `cash_paid − total ≈ change`. |
| **Balance sheet** | `Total Capital & Liabilities ≈ Total Assets`; `Σ capital & liability rows ≈ reported total`; `Σ asset rows ≈ reported total`. |
| **Profit & loss** | `Interest Earned + Other Income ≈ Total Income`; `Interest Expended + Operating Expenses + Provisions ≈ Total Expenditure`; `Total Income − Total Expenditure ≈ Net Profit`; `Net Profit − Minority Interest + Share of Associates ≈ Profit attributable to the Group`; `Current Profit + Brought Forward ≈ Total Available for Appropriation`. |
| **Cash flow** | `Operating + Investing + Financing + FX ≈ Net Increase in Cash`; `Opening + Net Increase + Amalgamation adjustments ≈ Closing Cash`. |

Bracketed figures are parsed as negatives, and every check runs **once per
reporting period** present in the document.

## Persistence

`ProcessedDocument` stores the document name (unique), type, status, the full
result JSON and timestamps. `DocumentRepository` upserts by name, so
reprocessing replaces the previous result and get-by-name returns the latest;
the dashboard list is served from the same table ordered by last update.

SQLite by default; set `IDEV_DATABASE_URL` to a Postgres/MySQL URL for
deployment — no code change, and `postgres://` URLs from managed providers are
rewritten to the driver form SQLAlchemy 2 expects.

## Deployment

Both frontend and API are one FastAPI app, so a single service covers both.
The OCR path needs `tesseract-ocr` and `poppler-utils`, so deploy the
**Docker** image rather than a plain Python runtime.

**Render (blueprint included):** push the repo, then *New → Blueprint* and pick
it up — [`render.yaml`](render.yaml) provisions the web service from
`backend/Dockerfile`, attaches a free Postgres instance, and sets
`/api/v1/health` as the health check. Set `IDEV_LLM_API_KEY` in the dashboard
only if you want the optional LLM pass.

**Railway / Koyeb / Fly:** point the service at `backend/Dockerfile` with the
repository root as build context, expose `$PORT`, and set `IDEV_DATABASE_URL`.

```bash
# The image, verbatim, as deployed:
docker build -f backend/Dockerfile -t doc-intelligence .
docker run -p 8000:8000 -e IDEV_DATABASE_URL=sqlite:///./data/documents.db doc-intelligence
```

A [`Procfile`](Procfile) and [`Aptfile`](Aptfile) are included for
buildpack-based platforms. Note that a free instance's filesystem is ephemeral —
use the managed database so processed results survive a restart.

## Testing

```bash
cd backend && pytest            # 42 tests
```

Covering file validation (type sniffing, empty, corrupted, page limit, size
limit), the layout engine (row rebuilding, period detection, lone-value column
assignment, schedule-column rejection, rejoining split figures), invoice
parsing and its refusal to read headings or OCR noise as values, every document
type's financial checks, the LLM pass (disabled, recovery, failure fallback),
the API flow (process → get-by-name → list), error envelopes, and the HTML
routes.

[`sample_outputs/`](sample_outputs/) holds real responses produced by this code:
all four document types, a two-page statement, scanned receipts (one that
reconciles, one whose tax line OCR could not read), a validation failure, an
unreadable scan, the dashboard list, and every error case.

## Measured accuracy on the provided dataset

Running all 30 statements in the supplied dataset (2017–2026) through the
pipeline and counting how many financial checks reconcile — a check only passes
if the numbers extracted from the document genuinely add up:

| Document type | PASS | FAIL | NOT_APPLICABLE |
| --- | --- | --- | --- |
| Balance sheet | 44 | 1 | 15 |
| Profit & loss | 72 | 6 | 22 |
| Cash flow | 32 | 0 | 8 |
| **Total** | **148** | **7** | **45** |

Clean scans reconcile fully (2017, 2018, 2023, 2026 pass every check). The
`NOT_APPLICABLE` results concentrate in the 2020–2022 scans, where OCR cannot
recover the row labels at all.

Every default in the OCR path was chosen by re-running this benchmark rather
than by assumption:

| Change | Result |
| --- | --- |
| Starting point (heading-based sections, colour input) | 132 PASS / 8 FAIL / 60 N/A |
| Sections also open on their first line item, not just a heading | 136 / 8 / 56 |
| Greyscale before OCR | 144 / 11 / 45 |
| Rejoin figures OCR split in half | **148 / 7 / 45** |
| 300 DPI instead of 200 | 127 / 16 / 57 — *rejected* |
| Second colour OCR pass unioned with the first | 149 / 7 / 44 for 2× the latency — *rejected* |

## Known limitations

- **OCR quality is the binding constraint.** On the 2020–2022 statements
  Tesseract drops entire row labels (`a 135,936.41`, `a) URE`), so those fields
  are reported `null` and their checks `NOT_APPLICABLE`. A commercial OCR
  service (Google Document AI, Textract) or the optional LLM pass is the way to
  close this gap; the deterministic path cannot invent what OCR did not read.
- **Statement parsing is tuned to this dataset's banking format.** Sections are
  recognised by their headings *and* by the line labels that open them, and both
  follow Indian bank statement conventions.
  Other layouts still get every labelled row via `line_items`, but the named
  fields and the per-type checks may come back `NOT_APPLICABLE`.
- **Receipt-style invoices extract fewer fields.** The SROIE images are noisy;
  line items are only captured when a row cleanly matches the
  description/qty/price/amount shape. A vendor name is only accepted from the
  top of the document and only when it reads like words, so an unreadable
  header returns `null` rather than a scrap of OCR noise.
- **Period labels degrade to `period_1`/`period_2`** when the header is too
  damaged to read a date (2024 balance sheet OCRs as `March a1,`). Values are
  still assigned to the correct columns.
- **A misread digit surfaces as a validation FAIL.** That is the check doing its
  job, but it means a FAIL is not proof the source document is wrong.
- **Processing is synchronous**, roughly 2–6 s per scanned page, dominated by
  Tesseract. Free-tier instances are slower still and cold-start.
- **No authentication.** The API is open, as the case study scope allows.

## What I would change for production

- Move processing to a worker queue (Celery/RQ or a task runner) and return a
  job id, so a slow scan never holds an HTTP connection open.
- Put the uploaded document in object storage (S3/GCS) and keep only a
  reference in the database; today the file is processed and discarded.
- Add authentication/authorisation and per-tenant isolation, plus rate limiting
  on the upload endpoint.
- Add a golden-file regression suite over a labelled corpus so extraction
  accuracy is tracked per change, rather than measured ad hoc as it is here.
- Run a better OCR engine, and treat the LLM pass as standard with cost controls
  and caching rather than an opt-in.
- Ship metrics/tracing (OpenTelemetry) for per-stage latency and validation
  outcomes, alert on the FAIL rate, and add DB migrations (Alembic) instead of
  `create_all`.

## AI assistance declaration

This solution was built with **Claude (Anthropic)** used as a coding assistant
throughout — scaffolding the FastAPI/service layout, drafting the parsing and
validation code, writing tests and this documentation.

The work that determined the outcome was empirical and is reproducible from the
repository: benchmarking all 30 dataset statements to find that the first
extractor was over-fitted to the 2017 layout, diagnosing why lone values landed
in the wrong period column, measuring PSM and DPI settings rather than assuming
them, and tracing the duplicated line-item lookups that made the validator check
different numbers than the extractor reported. Each fix was verified by
re-running that benchmark and the test suite; the accuracy table above is that
measurement, not an estimate.

The optional LLM pass calls Claude at runtime; it is disabled unless an API key
is supplied, and the platform is fully deterministic without it.
