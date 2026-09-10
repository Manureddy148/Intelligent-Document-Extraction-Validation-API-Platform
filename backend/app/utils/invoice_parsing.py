import re
from dataclasses import dataclass, field

_DATE_RE = re.compile(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}[/-]\d{1,2}[/-]\d{1,2})\b")
_TIME_RE = re.compile(r"\b(\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)?)\b", re.IGNORECASE)
# OCR frequently reads a decimal point as a comma ("1,72" for 1.72), so a
# two-digit group after a comma is a decimal while a three-digit group is a
# thousands separator.
_MONEY_RE = re.compile(r"(-?\d[\d,]*[.,]\d{2})(?!\d)")
_TAX_RATE_RE = re.compile(r"\b(?:GST|SST|VAT|TAX)\s*[:\-]?\s*(\d{1,2}(?:\.\d+)?)\s*%", re.IGNORECASE)
_TAX_AMOUNT_INLINE_RE = re.compile(
    r"\b(?:GST|SST|VAT|TAX)\s*(?:\d{1,2}(?:\.\d+)?\s*%)?\s*[+:\-]?\s*(\d[\d,]*\.\d{2})\b", re.IGNORECASE
)
# The trailing \b on the keyword stops "inv" matching inside the word
# "INVOICE" (which would capture "OICE" from a "TAX INVOICE" heading), and the
# captured reference must contain a digit so a following word is not mistaken
# for an identifier.
_TAX_ID_RE = re.compile(
    r"\b(?:GST|SST|VAT|TAX|TIN|GSTIN)\b\s*(?:No\.?|ID|Reg(?:istration)?(?:\s*No\.?)?)?\s*[:#]?\s*([A-Z0-9][A-Z0-9\-/]{5,})",
    re.IGNORECASE,
)
_INVOICE_NO_RE = re.compile(
    r"\b(?:invoice|receipt|bill|document|doc|reference|ref|trn|inv)\b\s*(?:no\.?|number|#|id)?\s*[:#]?\s*([A-Z0-9][A-Z0-9\-/]{2,})",
    re.IGNORECASE,
)
_HAS_DIGIT_RE = re.compile(r"\d")
_TOTAL_QTY_RE = re.compile(r"total\s*(?:qty|quantity)\s*[:\-]?\s*(\d+(?:[.,]\d+)?)", re.IGNORECASE)
# Receipt headings and OCR noise are not vendor names. A short scrap like
# "cudd" reads as a real value in the output, which is worse than reporting
# that the name could not be read.
_DOCUMENT_HEADING_RE = re.compile(
    r"^\W*(?:tax\s+)?(?:invoice|receipt|bill|statement|counter|cashier|customers?|orders?|payments?)\b", re.IGNORECASE
)
# A line like "Qty UOM U.Price Amt Tax Code" is the line-item column header.
_COLUMN_HEADER_WORDS = ("qty", "uom", "price", "amt", "amount", "description", "item", "code", "disc")
_ADDRESS_HINT_RE = re.compile(r"\b(?:jalan|jln|no\.?\s*\d|street|st\.|road|rd\.|taman|lot|floor|avenue|ave)\b", re.IGNORECASE)

# "<description> <qty> <unit price> <amount>" and "<qty> <description> <unit price> <amount>"
_LINE_ITEM_TRAILING_QTY_RE = re.compile(
    r"^(?P<desc>[A-Za-z][A-Za-z0-9 /,'&.()\-]{2,60}?)\s+"
    r"(?P<qty>\d+(?:\.\d+)?)\s+"
    r"(?P<unit_price>\d[\d,]*\.\d{2})\s+"
    r"(?P<amount>\d[\d,]*\.\d{2})\s*[A-Z]{0,3}$"
)
_LINE_ITEM_LEADING_QTY_RE = re.compile(
    r"^(?P<qty>\d+(?:\.\d+)?)\s+"
    r"(?P<desc>[A-Za-z][A-Za-z0-9 /,'&.()\-]{2,60}?)\s+"
    r"(?P<unit_price>\d[\d,]*\.\d{2})\s+"
    r"(?P<amount>\d[\d,]*\.\d{2})\s*[A-Z]{0,3}$"
)

_VENDOR_NAME_SEARCH_ROWS = 6

# "1x 12.50 12.50 SR" - quantity, unit price, line amount, tax code.
_QTY_X_PRICE_RE = re.compile(
    r"^(?P<qty>\d+(?:\.\d+)?)\s*[xX]\s*(?P<unit_price>\d[\d,]*[.,]\d{2})\s+"
    r"(?P<amount>\d[\d,]*[.,]\d{2})\s*[A-Za-z]{0,3}$"
)
_SUBTOTAL_LABEL_RE = re.compile(r"sub\s*total|total\s*\(?\s*(?:excl|before|exclusive)", re.IGNORECASE)
_TAX_LABEL_RE = re.compile(r"\b(?:gst|sst|vat|tax)\b.*(?:payable|amount|charged)|\b(?:gst|sst|vat)\s*\(", re.IGNORECASE)

_TOTAL_EXCLUSIONS = ("qty", "quantity", "item", "count", "summary")


def _parse_money(token: str) -> float | None:
    token = token.strip()
    if re.fullmatch(r"-?\d+,\d{2}", token):  # "1,72" -> 1.72
        token = token.replace(",", ".")
    try:
        return float(token.replace(",", ""))
    except ValueError:
        return None


def _money_values(line: str) -> list[float]:
    return [value for value in (_parse_money(m) for m in _MONEY_RE.findall(line)) if value is not None]


def _last_money(line: str) -> float | None:
    values = _money_values(line)
    return values[-1] if values else None


@dataclass
class InvoiceLineItem:
    description: str
    quantity: float
    unit_price: float
    amount: float
    page_number: int | None = None
    source_text: str | None = None


@dataclass
class InvoiceContext:
    vendor_name: str | None = None
    vendor_address: str | None = None
    vendor_tax_id: str | None = None
    customer_name: str | None = None
    invoice_number: str | None = None
    invoice_date: str | None = None
    invoice_time: str | None = None
    currency: str | None = None
    subtotal: float | None = None
    tax_amount: float | None = None
    tax_rate_percent: float | None = None
    tax_inclusive: bool | None = None
    discount: float | None = None
    total_amount: float | None = None
    total_quantity: float | None = None
    cash_paid: float | None = None
    change: float | None = None
    line_items: list[InvoiceLineItem] = field(default_factory=list)
    evidence: dict[str, tuple[int, str]] = field(default_factory=dict)

    def record(self, key: str, page_number: int, line: str) -> None:
        self.evidence[key] = (page_number, line)


def parse_invoice(lines_with_pages: list[tuple[int, str]]) -> InvoiceContext:
    """Best-effort field extraction for invoices and point-of-sale receipts.

    Every value is taken verbatim from an OCR line; nothing is inferred when a
    line is absent, so unreadable fields stay null.
    """
    ctx = InvoiceContext()
    previous_description: str | None = None
    full_text = "\n".join(line for _, line in lines_with_pages)

    for row_index, (page_number, raw_line) in enumerate(lines_with_pages):
        line = raw_line.strip()
        lowered = line.lower()

        letters = re.sub(r"[^A-Za-z]", "", line)
        real_words = [w for w in re.split(r"\s+", line) if len(re.sub(r"[^A-Za-z]", "", w)) >= 3]
        alpha_ratio = len(letters) / max(len(re.sub(r"\s", "", line)), 1)
        looks_like_a_name = len(letters) >= 5 and len(real_words) >= 2 and alpha_ratio >= 0.7
        if (
            ctx.vendor_name is None
            and row_index < _VENDOR_NAME_SEARCH_ROWS
            and looks_like_a_name
            and not _DOCUMENT_HEADING_RE.match(line)
            and sum(word in lowered for word in _COLUMN_HEADER_WORDS) < 2
            and not _DATE_RE.search(line)
            and not _MONEY_RE.search(line)
        ):
            ctx.vendor_name = line
            ctx.record("vendor_name", page_number, line)
        elif ctx.vendor_address is None and _ADDRESS_HINT_RE.search(line):
            ctx.vendor_address = line
            ctx.record("vendor_address", page_number, line)

        if ctx.vendor_tax_id is None:
            tax_id = _TAX_ID_RE.search(line)
            if tax_id and not _MONEY_RE.search(line) and _HAS_DIGIT_RE.search(tax_id.group(1)):
                ctx.vendor_tax_id = tax_id.group(1)
                ctx.record("vendor_tax_id", page_number, line)

        if ctx.invoice_number is None:
            invoice_no = _INVOICE_NO_RE.search(line)
            if invoice_no and not _MONEY_RE.search(line) and _HAS_DIGIT_RE.search(invoice_no.group(1)):
                ctx.invoice_number = invoice_no.group(1)
                ctx.record("invoice_number", page_number, line)

        if ctx.invoice_date is None:
            date_match = _DATE_RE.search(line)
            if date_match:
                ctx.invoice_date = date_match.group(1)
                ctx.record("invoice_date", page_number, line)
                time_match = _TIME_RE.search(line)
                if time_match:
                    ctx.invoice_time = time_match.group(1)
                    ctx.record("invoice_time", page_number, line)

        if ctx.tax_rate_percent is None:
            rate_match = _TAX_RATE_RE.search(line)
            if rate_match:
                ctx.tax_rate_percent = float(rate_match.group(1))
                ctx.record("tax_rate_percent", page_number, line)

        if ctx.tax_amount is None and "total" not in lowered:
            tax_match = _TAX_AMOUNT_INLINE_RE.search(line)
            value = _parse_money(tax_match.group(1)) if tax_match else None
            if value is None and _TAX_LABEL_RE.search(line):
                value = _last_money(line)
            if value is not None:
                ctx.tax_amount = value
                ctx.record("tax_amount", page_number, line)

        is_subtotal_line = bool(_SUBTOTAL_LABEL_RE.search(line))
        if is_subtotal_line:
            value = _last_money(line)
            if value is not None:
                ctx.subtotal = value
                ctx.record("subtotal", page_number, line)

        if "discount" in lowered:
            value = _last_money(line)
            if value is not None:
                ctx.discount = value
                ctx.record("discount", page_number, line)

        quantity_match = _TOTAL_QTY_RE.search(line)
        if quantity_match:
            ctx.total_quantity = float(quantity_match.group(1).replace(",", "."))
            ctx.record("total_quantity", page_number, line)
        elif "total" in lowered and not is_subtotal_line and not any(token in lowered for token in _TOTAL_EXCLUSIONS):
            value = _last_money(line)
            if value is not None:
                ctx.total_amount = value
                ctx.record("total_amount", page_number, line)
                if re.search(r"includ\w*", lowered):
                    ctx.tax_inclusive = True

        if re.search(r"\bcash\b", lowered) and "cashier" not in lowered:
            value = _last_money(line)
            if value is not None:
                ctx.cash_paid = value
                ctx.record("cash_paid", page_number, line)

        if "change" in lowered:
            value = _last_money(line)
            if value is not None:
                ctx.change = value
                ctx.record("change", page_number, line)

        qty_x_match = _QTY_X_PRICE_RE.match(line)
        if qty_x_match and previous_description:
            unit_price = _parse_money(qty_x_match.group("unit_price"))
            amount = _parse_money(qty_x_match.group("amount"))
            if unit_price is not None and amount is not None:
                ctx.line_items.append(
                    InvoiceLineItem(
                        description=previous_description,
                        quantity=float(qty_x_match.group("qty")),
                        unit_price=unit_price,
                        amount=amount,
                        page_number=page_number,
                        source_text=f"{previous_description} / {line}",
                    )
                )
            previous_description = None
            continue

        for pattern in (_LINE_ITEM_TRAILING_QTY_RE, _LINE_ITEM_LEADING_QTY_RE):
            item_match = pattern.match(line)
            if item_match:
                ctx.line_items.append(
                    InvoiceLineItem(
                        description=item_match.group("desc").strip(),
                        quantity=float(item_match.group("qty")),
                        unit_price=float(item_match.group("unit_price").replace(",", "")),
                        amount=float(item_match.group("amount").replace(",", "")),
                        page_number=page_number,
                        source_text=line,
                    )
                )
                break
        else:
            if not _MONEY_RE.search(line) and len(re.sub(r"[^A-Za-z]", "", line)) >= 3:
                previous_description = line
            elif _MONEY_RE.search(line):
                previous_description = None

    if ctx.tax_inclusive is None and ctx.tax_amount is not None and ctx.subtotal is None:
        ctx.tax_inclusive = True

    for pattern, code in ((r"\bRM\b|\bMYR\b", "MYR"), (r"\bUSD\b|\$", "USD"), (r"\bINR\b|₹", "INR"), (r"\bEUR\b|€", "EUR")):
        if re.search(pattern, full_text):
            ctx.currency = code
            break

    return ctx
