"""Renders docs/architecture.png. Run: python docs/generate_architecture_diagram.py"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1680, 1180
BG = "#f4f6f8"
INK = "#1f2933"
MUTED = "#61707d"
ACCENT = "#1f5f8b"
BORDER = "#c8d2dc"
LANE_FILL = "#ffffff"
SERVICE_FILL = "#eaf2f8"
EXTERNAL_FILL = "#fff6da"
STORE_FILL = "#e3f5e9"

FONT_DIR = "/usr/share/fonts/truetype/dejavu"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(f"{FONT_DIR}/{name}", size)
    except OSError:
        return ImageFont.load_default()


def box(draw, xy, title, lines=(), fill=LANE_FILL, title_size=19, radius=10):
    x0, y0, x1, y1 = xy
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=BORDER, width=2)
    draw.text((x0 + 16, y0 + 12), title, font=font(title_size, bold=True), fill=INK)
    y = y0 + 16 + title_size + 8
    for line in lines:
        draw.text((x0 + 16, y), line, font=font(14), fill=MUTED)
        y += 20


def arrow(draw, start, end, color=ACCENT, width=3, label=None):
    x0, y0 = start
    x1, y1 = end
    draw.line([start, end], fill=color, width=width)
    head = 9
    if x0 == x1:  # vertical
        direction = 1 if y1 > y0 else -1
        draw.polygon(
            [(x1, y1), (x1 - head, y1 - head * direction), (x1 + head, y1 - head * direction)], fill=color
        )
    else:  # horizontal
        direction = 1 if x1 > x0 else -1
        draw.polygon(
            [(x1, y1), (x1 - head * direction, y1 - head), (x1 - head * direction, y1 + head)], fill=color
        )
    if label:
        draw.text(((x0 + x1) // 2 + 10, (y0 + y1) // 2 - 20), label, font=font(13), fill=MUTED)


def main() -> None:
    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)

    draw.text((40, 30), "Intelligent Document Extraction, Validation & API Platform", font=font(30, bold=True), fill=INK)
    draw.text((40, 72), "Request path for POST /api/v1/documents/process", font=font(17), fill=MUTED)

    # --- Client layer
    box(
        draw,
        (40, 120, 560, 250),
        "Browser (frontend/)",
        [
            "dashboard.html - document type + file upload, results table",
            "document_result.html - fields, tables, validation, raw JSON",
            "static/js - calls the REST API with fetch()",
        ],
    )

    # --- API layer
    box(
        draw,
        (40, 300, 560, 470),
        "FastAPI application (backend/app/main.py)",
        [
            "POST /api/v1/documents/process   (multipart upload)",
            "GET  /api/v1/documents           (dashboard list)",
            "GET  /api/v1/documents/{name}    (latest result)",
            "GET  /api/v1/health              (liveness)",
            "GET  /docs                       (Swagger / OpenAPI)",
            "AppError handlers -> {\"error\": {code, message}}",
        ],
    )

    arrow(draw, (300, 250), (300, 300), label="HTTPS")

    # --- Pipeline
    draw.rounded_rectangle((620, 120, 1240, 1010), radius=14, fill="#ffffff", outline=BORDER, width=2)
    draw.text((644, 136), "Processing pipeline (services/)", font=font(21, bold=True), fill=INK)
    draw.text((644, 166), "orchestrated by document_service.py", font=font(14), fill=MUTED)

    stages = [
        (
            "1. document_validation_service.py",
            ["PDF / JPG / PNG by magic bytes, not the", "declared type - integrity, page limit (<=3)"],
        ),
        (
            "2. ocr_service.py",
            ["Native PDF text via pdfplumber, else", "rasterise + Tesseract; keeps word boxes"],
        ),
        (
            "3. extraction_service.py + utils/layout.py",
            ["Rebuilds rows by y-position, assigns each", "number to a period column by x-position"],
        ),
        (
            "4. llm_extraction_service.py  (optional)",
            ["Fills only fields left null; skipped", "entirely when no API key is configured"],
        ),
        (
            "5. financial_validation_service.py",
            ["Per-type checks with formula, operands,", "variance, PASS / FAIL / NOT_APPLICABLE"],
        ),
    ]
    y = 200
    for title, lines in stages:
        box(draw, (650, y, 1210, y + 118), title, lines, fill=SERVICE_FILL, title_size=17)
        if y + 118 < 900:
            arrow(draw, (930, y + 118), (930, y + 152))
        y += 152

    arrow(draw, (560, 380), (620, 380))

    # --- External dependencies
    box(
        draw,
        (1290, 300, 1640, 430),
        "External / system",
        ["Tesseract OCR (pytesseract)", "Poppler pdftoppm (pdf2image)", "Anthropic API - optional, key-gated"],
        fill=EXTERNAL_FILL,
    )
    arrow(draw, (1210, 365), (1290, 365), color=MUTED, width=2)

    # --- Persistence
    box(
        draw,
        (620, 1040, 1240, 1150),
        "Persistence (repositories/ + models/)",
        [
            "DocumentRepository - upsert by document name, latest result wins",
            "SQLAlchemy ORM -> SQLite by default, Postgres/MySQL via DATABASE_URL",
        ],
        fill=STORE_FILL,
    )
    arrow(draw, (930, 1010), (930, 1040), label="store result")

    # --- Response note
    box(
        draw,
        (40, 520, 560, 700),
        "Structured JSON response",
        [
            "document_name, document_type, processing_status",
            "file_validation { type, readable, page_count }",
            "extracted_data { field: value, page_number,",
            "                 source_text }  + line_items[]",
            "validation { checks[], overall_status, issues[] }",
            "processing_metadata { ocr_used, timings }",
        ],
    )
    arrow(draw, (620, 620), (560, 620), label="result")

    box(
        draw,
        (40, 750, 560, 900),
        "Cross-cutting",
        [
            "core/config.py    - env-driven settings (no secrets in code)",
            "core/logging.py   - structured application logging",
            "core/exceptions.py- typed errors -> HTTP status codes",
            "core/database.py  - engine + session lifecycle",
            "tests/            - 31 automated tests",
        ],
    )

    output = Path(__file__).resolve().parent / "architecture.png"
    image.save(output)
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
