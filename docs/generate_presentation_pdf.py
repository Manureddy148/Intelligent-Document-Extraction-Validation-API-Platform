"""Renders docs/solution_presentation.pdf (the slide deck as a PDF).

Mirrors docs/solution_presentation.pptx so evaluators can read the deck without
PowerPoint. Run: python docs/generate_presentation_pdf.py
"""

from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

DOCS = Path(__file__).resolve().parent
PAGE = (960, 540)  # 16:9 points

NAVY = HexColor("#1E2761")
ICE = HexColor("#CADCFC")
WHITE = HexColor("#FFFFFF")
INK = HexColor("#1F2933")
MUTED = HexColor("#5A6B7B")
CARD = HexColor("#F4F6F8")
BORDER = HexColor("#DDE4EA")
PASS = HexColor("#1B7A3D")
FAIL = HexColor("#B3261E")
NA = HexColor("#C79A00")

SERIF = "Times-Bold"
SANS = "Helvetica"
SANS_BOLD = "Helvetica-Bold"
MONO = "Courier"


def wrap(text, font, size, width, c):
    words, lines, line = text.split(), [], ""
    for word in words:
        probe = f"{line} {word}".strip()
        if c.stringWidth(probe, font, size) <= width:
            line = probe
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def para(c, text, x, y, width, font=SANS, size=11, colour=MUTED, leading=15):
    c.setFont(font, size)
    c.setFillColor(colour)
    for line in wrap(text, font, size, width, c):
        c.drawString(x, y, line)
        y -= leading
    return y


def bullets(c, items, x, y, width, size=11, colour=INK, leading=15, gap=6):
    for item in items:
        c.setFillColor(colour)
        c.setFont(SANS, size)
        c.drawString(x, y, "•")
        for i, line in enumerate(wrap(item, SANS, size, width - 14, c)):
            c.drawString(x + 14, y, line)
            if i < len(wrap(item, SANS, size, width - 14, c)) - 1:
                y -= leading
        y -= leading + gap
    return y


def card(c, x, y, w, h, fill=CARD):
    c.setFillColor(fill)
    c.setStrokeColor(BORDER)
    c.setLineWidth(1)
    c.roundRect(x, y, w, h, 6, stroke=1, fill=1)


def heading(c, title, subtitle=None):
    c.setFillColor(INK)
    c.setFont(SERIF, 26)
    c.drawString(48, PAGE[1] - 58, title)
    if subtitle:
        c.setFillColor(MUTED)
        c.setFont(SANS, 12)
        c.drawString(48, PAGE[1] - 80, subtitle)


def slide_title(c):
    c.setFillColor(NAVY)
    c.rect(0, 0, *PAGE, stroke=0, fill=1)
    c.setFillColor(WHITE)
    c.setFont(SERIF, 32)
    c.drawString(64, 330, "Intelligent Document Extraction,")
    c.drawString(64, 292, "Validation & API Platform")
    c.setFillColor(ICE)
    c.setFont(SANS, 14)
    c.drawString(64, 250, "Invoices  ·  Balance Sheets  ·  Profit & Loss  ·  Cash Flow Statements")
    c.setFont(SANS, 11)
    c.drawString(64, 226, "OCR + layout-aware extraction · financial reconciliation · REST API · dashboard")
    c.setFillColor(HexColor("#8FA6C4"))
    c.setFont(SANS, 10)
    c.drawString(64, 70, "AI Engineer Internship — Technical Case Study")


def slide_problem(c):
    heading(c, "The problem", "The same numbers arrive in inconsistent, often scanned, layouts")
    stages = [
        ("1", "Validate", "Type, integrity and page limit — before OCR"),
        ("2", "Read", "Native PDF text, or rasterise and OCR"),
        ("3", "Extract", "Fields and every table row, grounded"),
        ("4", "Reconcile", "Recompute the financial relationships"),
        ("5", "Serve", "Store, expose via API and dashboard"),
    ]
    for i, (n, title, body) in enumerate(stages):
        x = 48 + i * 176
        card(c, x, 300, 164, 128)
        c.setFillColor(NAVY)
        c.circle(x + 26, 404, 14, stroke=0, fill=1)
        c.setFillColor(WHITE)
        c.setFont(SANS_BOLD, 12)
        c.drawCentredString(x + 26, 400, n)
        c.setFillColor(INK)
        c.setFont(SERIF, 13)
        c.drawString(x + 12, 372, title)
        para(c, body, x + 12, 356, 142, size=9.5, leading=12)
    card(c, 48, 150, 864, 128, HexColor("#F0F5FA"))
    c.setFillColor(NAVY)
    c.setFont(SERIF, 14)
    c.drawString(64, 252, "Four document types in scope")
    bullets(
        c,
        [
            "Invoice — header fields, line items, tax, cash and change",
            "Balance sheet — capital & liabilities and assets, per period",
            "Profit & loss — income, expenditure, profit and appropriations",
            "Cash flow — operating, investing, financing, opening/closing cash",
        ],
        64, 228, 830, size=10.5, leading=13, gap=1,
    )


def slide_architecture(c):
    heading(c, "Architecture", "One FastAPI application serves the API and the dashboard")
    image = ImageReader(str(DOCS / "architecture.png"))
    iw, ih = image.getSize()
    width = 830
    height = width * ih / iw
    c.drawImage(image, 65, 430 - height, width=width, height=height, mask="auto")


def slide_insight(c):
    heading(c, "Why line-by-line OCR text is not enough", "The insight that drove the design")
    card(c, 48, 300, 420, 132, HexColor("#FDF0EF"))
    c.setFillColor(FAIL)
    c.setFont(SERIF, 14)
    c.drawString(64, 408, "Reading OCR text as lines")
    c.setFillColor(INK)
    c.setFont(MONO, 11)
    c.drawString(64, 386, "Total    743,732,155")
    para(c, "A row filled in for only one year gives no clue which year it belongs to — and OCR often "
            "emits labels and numbers as separate blocks entirely.", 64, 368, 390, size=9.5, leading=12)

    card(c, 492, 300, 420, 132, HexColor("#EDF7F0"))
    c.setFillColor(PASS)
    c.setFont(SERIF, 14)
    c.drawString(508, 408, "Reading the page as a table")
    c.setFillColor(INK)
    c.setFont(MONO, 10)
    c.drawString(508, 386, "Total · x≈1540 → column 2 → 31-Mar-16")
    para(c, "Word boxes are kept from both PDF text and OCR. Rows are rebuilt by vertical position; each "
            "number is assigned to a period column by its right edge.", 508, 368, 390, size=9.5, leading=12)

    card(c, 48, 120, 864, 156, HexColor("#F0F5FA"))
    c.setFillColor(NAVY)
    c.setFont(SERIF, 14)
    c.drawString(64, 252, "Measured, not assumed")
    bullets(
        c,
        [
            "Benchmarked all 30 dataset statements; the first extractor was over-fitted to one year",
            "Compared page-segmentation modes and DPI — 300 DPI measured worse than 200",
            "Extraction and validation had drifted to two different sets of field lookups; unified them",
            "A second colour OCR pass gained one check for twice the latency, so it was left out",
        ],
        64, 228, 830, size=10.5, leading=13, gap=2,
    )


def slide_validation(c):
    heading(c, "Financial validation", "Each check reports formula, operands, calculated vs reported, variance, status")
    rules = [
        ("Invoice", "qty × unit price ≈ line amount · Σ lines ≈ subtotal · subtotal + tax − discount ≈ total · cash − total ≈ change"),
        ("Balance sheet", "Total Capital & Liabilities ≈ Total Assets · Σ component rows ≈ each reported total"),
        ("Profit & loss", "Interest + Other Income ≈ Total Income · Income − Expenditure ≈ Net Profit · − Minority ≈ Attributable"),
        ("Cash flow", "Operating + Investing + Financing + FX ≈ Net Increase · Opening + Net Increase ≈ Closing Cash"),
    ]
    y = 386
    for name, body in rules:
        card(c, 48, y, 530, 62, WHITE)
        c.setFillColor(NAVY)
        c.setFont(SERIF, 12)
        c.drawString(62, y + 40, name)
        para(c, body, 62, y + 26, 500, size=8.5, leading=11)
        y -= 72
    card(c, 596, 98, 316, 350, HexColor("#F0F5FA"))
    c.setFillColor(NAVY)
    c.setFont(SERIF, 14)
    c.drawString(612, 424, "Honest by design")
    bullets(
        c,
        [
            "Missing operand → NOT_APPLICABLE, never a failure",
            "A partly-read section is not summed — an unread row would count as zero",
            "Tolerance: max(1.0, 1% of the larger value)",
            "Bracketed figures parse as negative",
            "Every check runs once per reporting period",
            "Missing values stay null — never guessed",
        ],
        612, 400, 290, size=10, leading=12, gap=6,
    )


def slide_results(c):
    heading(c, "Measured results", "All 50 documents in the provided dataset, 297 checks")
    rows = [
        ("Balance sheet", 45, 0, 15),
        ("Profit & loss", 76, 0, 24),
        ("Cash flow", 32, 0, 8),
        ("Invoice", 27, 2, 20),
    ]
    max_value = 76
    base_y, chart_h, bar_w = 150, 210, 26
    c.setFillColor(MUTED)
    c.setFont(SANS, 9)
    for i, (label, p, f, n) in enumerate(rows):
        gx = 84 + i * 128
        for j, (value, colour) in enumerate(((p, PASS), (f, FAIL), (n, NA))):
            h = (value / max_value) * chart_h
            c.setFillColor(colour)
            c.rect(gx + j * (bar_w + 6), base_y, bar_w, max(h, 1), stroke=0, fill=1)
            c.setFillColor(INK)
            c.setFont(SANS_BOLD, 9)
            c.drawCentredString(gx + j * (bar_w + 6) + bar_w / 2, base_y + max(h, 1) + 5, str(value))
        c.setFillColor(MUTED)
        c.setFont(SANS, 10)
        c.drawCentredString(gx + 45, base_y - 16, label)
    c.setStrokeColor(BORDER)
    c.line(70, base_y, 560, base_y)
    for j, (name, colour) in enumerate((("PASS", PASS), ("FAIL", FAIL), ("NOT_APPLICABLE", NA))):
        c.setFillColor(colour)
        c.rect(90 + j * 130, 106, 11, 11, stroke=0, fill=1)
        c.setFillColor(MUTED)
        c.setFont(SANS, 9)
        c.drawString(106 + j * 130, 108, name)

    stats = [
        ("228", "checks reconcile exactly", PASS),
        ("2", "genuine discrepancies, verified by hand", FAIL),
        ("67", "not applicable — nothing to reconcile", NA),
    ]
    for i, (n, label, colour) in enumerate(stats):
        y = 340 - i * 84
        card(c, 606, y, 306, 70, WHITE)
        c.setFillColor(colour)
        c.setFont(SERIF, 30)
        c.drawString(624, y + 26, n)
        c.setFillColor(MUTED)
        c.setFont(SANS, 10)
        for k, line in enumerate(wrap(label, SANS, 10, 190, c)):
            c.drawString(700, y + 40 - k * 13, line)
    para(c, "Clean scans reconcile fully; the not-applicable results concentrate in the 2020–2022 scans "
            "where OCR cannot recover row labels.", 606, 84, 306, size=8.5, leading=11)


def slide_api(c):
    heading(c, "API and dashboard", "One deployment serves both; Swagger is generated from the code")
    card(c, 48, 250, 380, 180, WHITE)
    endpoints = [
        ("POST", "/api/v1/documents/process", "upload and process"),
        ("GET", "/api/v1/documents", "dashboard list"),
        ("GET", "/api/v1/documents/{name}", "latest result"),
        ("GET", "/api/v1/health", "health check"),
        ("GET", "/docs", "Swagger / OpenAPI"),
    ]
    y = 400
    for verb, url, note in endpoints:
        c.setFillColor(NAVY)
        c.setFont(SANS_BOLD, 9)
        c.drawString(62, y, verb)
        c.setFillColor(INK)
        c.setFont(MONO, 8.5)
        c.drawString(96, y, url)
        c.setFillColor(MUTED)
        c.setFont(SANS, 8)
        c.drawString(300, y, note)
        y -= 30
    card(c, 48, 110, 380, 120, HexColor("#F0F5FA"))
    c.setFillColor(NAVY)
    c.setFont(SERIF, 12)
    c.drawString(62, 206, "Errors return one envelope")
    c.setFillColor(INK)
    c.setFont(MONO, 8)
    c.drawString(62, 186, '{ "error": { "code": "UNSUPPORTED_FILE_TYPE",')
    c.drawString(62, 174, '             "message": "Only PDF / JPG / PNG..." } }')
    para(c, "415 · 413 · 400 · 404 · 422 · 500 — never a stack trace", 62, 150, 350, size=9)

    image = ImageReader(str(DOCS / "screenshot_invoice_result.png"))
    iw, ih = image.getSize()
    width = 452
    height = min(width * ih / iw, 330)
    c.drawImage(image, 452, 108, width=width, height=height, mask="auto", preserveAspectRatio=True, anchor="n")


def slide_limits(c):
    c.setFillColor(NAVY)
    c.rect(0, 0, *PAGE, stroke=0, fill=1)
    c.setFillColor(WHITE)
    c.setFont(SERIF, 26)
    c.drawString(48, 470, "Limitations and what I'd change")
    c.setFillColor(ICE)
    c.setFont(SERIF, 14)
    c.drawString(48, 424, "Known today")
    c.drawString(500, 424, "For production")
    left = [
        "OCR quality is the binding constraint — the 2020–2022 scans lose row labels entirely",
        "Statement parsing is tuned to this dataset's banking format",
        "Noisy receipts yield fewer line items",
        "Synchronous processing, ~2–6 s per scanned page",
        "No authentication — per case study scope",
    ]
    right = [
        "Queue the work and return a job id instead of holding the connection",
        "Commercial OCR, with the LLM pass standard rather than opt-in",
        "Golden-file regression suite over a labelled corpus",
        "Object storage for source documents; Alembic migrations",
        "Auth, rate limiting, metrics and alerting on the FAIL rate",
    ]
    bullets(c, left, 48, 396, 400, size=10.5, colour=WHITE, leading=13, gap=8)
    bullets(c, right, 500, 396, 412, size=10.5, colour=WHITE, leading=13, gap=8)
    c.setFillColor(ICE)
    c.setFont(SANS, 10)
    c.drawCentredString(
        PAGE[0] / 2, 56, "Deterministic by default · every value traceable to its source row · nothing invented"
    )


def main() -> None:
    output = DOCS / "solution_presentation.pdf"
    c = canvas.Canvas(str(output), pagesize=PAGE)
    for slide in (
        slide_title, slide_problem, slide_architecture, slide_insight,
        slide_validation, slide_results, slide_api, slide_limits,
    ):
        c.setFillColor(HexColor("#FFFFFF"))
        c.rect(0, 0, *PAGE, stroke=0, fill=1)
        slide(c)
        c.showPage()
    c.save()
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
