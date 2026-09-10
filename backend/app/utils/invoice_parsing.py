import re
from dataclasses import dataclass, field

_DATE_RE = re.compile(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b")
_MONEY_RE = re.compile(r"(-?\d[\d,]*\.\d{2})")
_GST_INLINE_RE = re.compile(r"GST\s*\d{1,2}(?:\.\d+)?%\+?\s*([\d,]+\.\d{2})", re.IGNORECASE)
_LINE_ITEM_RE = re.compile(
    r"^(?P<desc>[A-Za-z][A-Za-z0-9 /,'&.\-]{2,50}?)\s+"
    r"(?P<qty>\d+(?:\.\d+)?)\s+"
    r"(?P<unit_price>\d+\.\d{2})\s+"
    r"(?P<amount>\d+\.\d{2})\s*$"
)


def _last_money_on_line(line: str) -> float | None:
    matches = _MONEY_RE.findall(line)
    if not matches:
        return None
    return float(matches[-1].replace(",", ""))


@dataclass
class InvoiceLineItem:
    description: str
    quantity: float
    unit_price: float
    amount: float


@dataclass
class InvoiceContext:
    vendor_name: str | None = None
    invoice_number: str | None = None
    invoice_date: str | None = None
    currency: str | None = None
    subtotal: float | None = None
    tax_amount: float | None = None
    discount: float | None = None
    total_amount: float | None = None
    cash_paid: float | None = None
    change: float | None = None
    line_items: list[InvoiceLineItem] = field(default_factory=list)
    evidence: dict[str, tuple[int, str]] = field(default_factory=dict)


def parse_invoice(lines_with_pages: list[tuple[int, str]]) -> InvoiceContext:
    ctx = InvoiceContext()
    full_text = "\n".join(line for _, line in lines_with_pages)

    for page_number, line in lines_with_pages:
        if ctx.vendor_name is None and len(line.strip()) >= 3 and not line.strip().isdigit():
            ctx.vendor_name = line.strip()

        lowered = line.lower()

        gst_match = _GST_INLINE_RE.search(line)
        if gst_match and ctx.tax_amount is None:
            ctx.tax_amount = float(gst_match.group(1).replace(",", ""))
            ctx.evidence["tax_amount"] = (page_number, line.strip())

        if "sub total" in lowered or "subtotal" in lowered:
            value = _last_money_on_line(line)
            if value is not None:
                ctx.subtotal = value
                ctx.evidence["subtotal"] = (page_number, line.strip())

        if "total" in lowered and "qty" not in lowered:
            value = _last_money_on_line(line)
            if value is not None:
                ctx.total_amount = value
                ctx.evidence["total_amount"] = (page_number, line.strip())

        if re.search(r"\bcash\b", lowered) and "cashier" not in lowered:
            value = _last_money_on_line(line)
            if value is not None:
                ctx.cash_paid = value
                ctx.evidence["cash_paid"] = (page_number, line.strip())

        if "change" in lowered:
            value = _last_money_on_line(line)
            if value is not None:
                ctx.change = value
                ctx.evidence["change"] = (page_number, line.strip())

        if ctx.invoice_number is None:
            ref_match = re.search(r"\b(?:TRN|Invoice\s*No\.?|Receipt\s*No\.?)\s*[:#]?\s*([A-Za-z0-9-]+)", line, re.IGNORECASE)
            if ref_match:
                ctx.invoice_number = ref_match.group(1)
                ctx.evidence["invoice_number"] = (page_number, line.strip())

        item_match = _LINE_ITEM_RE.match(line.strip())
        if item_match:
            ctx.line_items.append(
                InvoiceLineItem(
                    description=item_match.group("desc").strip(),
                    quantity=float(item_match.group("qty")),
                    unit_price=float(item_match.group("unit_price")),
                    amount=float(item_match.group("amount")),
                )
            )

    date_match = _DATE_RE.search(full_text)
    if date_match:
        ctx.invoice_date = date_match.group(1)

    if re.search(r"\bRM\b", full_text):
        ctx.currency = "MYR"
    elif re.search(r"\bUSD\b|\$", full_text):
        ctx.currency = "USD"
    elif re.search(r"\bINR\b|₹", full_text):
        ctx.currency = "INR"

    return ctx
