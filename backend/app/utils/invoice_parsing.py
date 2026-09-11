import re
from dataclasses import dataclass, field

_MONTH = r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
_DATE_RE = re.compile(
    r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}[/-]\d{1,2}[/-]\d{1,2}"
    rf"|(?:{_MONTH})[a-z]*\.?\s+\d{{1,2}},?\s+\d{{4}}"          # Oct. 13, 2023
    rf"|\d{{1,2}}\s+(?:{_MONTH})[a-z]*\.?\s+\d{{4}})\b",        # 13 Oct 2023
    re.IGNORECASE,
)
_TIME_RE = re.compile(r"\b(\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)?)\b", re.IGNORECASE)
# OCR frequently reads a decimal point as a comma ("1,72" for 1.72), so a
# two-digit group after a comma is a decimal while a three-digit group is a
# thousands separator.
_CURRENCY = r"(?:RM|MYR|USD|INR|EUR|GBP|Rs\.?|[$₹€£])"
_MONEY_RE = re.compile(rf"(?:{_CURRENCY}\s*)?(-?\d[\d,]*[.,]\d{{2}}|{_CURRENCY}\s*-?\d[\d,]*)(?!\d)", re.IGNORECASE)
_TAX_KEYWORDS = r"GST|SST|HST|PST|QST|VAT|TAX"
# The rate sits either side of the keyword: "GST 6%" but also "13% HST".
_TAX_RATE_RE = re.compile(
    rf"\b(?:{_TAX_KEYWORDS})\s*[:\-]?\s*(\d{{1,2}}(?:\.\d+)?)\s*%|(\d{{1,2}}(?:\.\d+)?)\s*%\s*(?:{_TAX_KEYWORDS})\b",
    re.IGNORECASE,
)
_TAX_AMOUNT_INLINE_RE = re.compile(
    rf"\b(?:{_TAX_KEYWORDS})\s*(?:\d{{1,2}}(?:\.\d+)?\s*%)?\s*[+:\-]?\s*(\d[\d,]*\.\d{{2}})\b", re.IGNORECASE
)
# A line that is nothing but a tax label and an amount: "13% HST: $ 146.64".
_TAX_ONLY_LINE_RE = re.compile(
    rf"^\W*(?:\d{{1,2}}(?:\.\d+)?\s*%\s*)?(?:{_TAX_KEYWORDS})\b"
    rf"\s*(?:@?\s*\d{{1,2}}(?:\.\d+)?\s*%)?\s*[:\-]?\s*(?:{_CURRENCY})?\s*(\d[\d,]*\.\d{{2}})\s*$",
    re.IGNORECASE,
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
_NUMBER_TOKEN_RE = re.compile(rf"(?:{_CURRENCY}\s*)?-?[\d,]+(?:[.,]\d{{2}})?", re.IGNORECASE)
_CURRENCY_ONLY_RE = re.compile(rf"^{_CURRENCY}$", re.IGNORECASE)

# "Sub Total", "Subtotal", "S. Total", "Total (excl. GST)" - all the net-of-tax
# line, as distinct from the amount actually payable.
_SUBTOTAL_LABEL_RE = re.compile(
    r"sub\s*total|\bs\.?\s*total\b|total\s*\(?\s*(?:excl|before|exclusive)", re.IGNORECASE
)
_TAX_LABEL_RE = re.compile(
    r"\b(?:gst|sst|hst|pst|qst|vat|tax)\b.*(?:payable|amount|charged|included\s+in\s+total)|\b(?:gst|sst|hst|pst|qst|vat)\s*\(",
    re.IGNORECASE,
)

_TOTAL_EXCLUSIONS = ("qty", "quantity", "item", "count", "summary")

# "GST @6% included in total RM 0.35" states the tax, not the total. Reading it
# as the total replaces a RM 6.20 bill with RM 0.35 and makes the change due
# look wrong by the whole value of the sale.
_TAX_LINE_LEAD_RE = re.compile(r"^\W*(?:gst|sst|hst|pst|qst|vat|tax|service\s+charge|svc)\b", re.IGNORECASE)


def _coheres(quantity: float, unit_price: float, amount: float) -> bool:
    """Does quantity * unit_price come out at the printed line amount?"""
    return abs(quantity * unit_price - amount) <= max(0.02, 0.01 * abs(amount))


def _plausible_columns(quantity: float, unit_price: float, amount: float) -> bool:
    """Is quantity * unit_price even the same size as the printed amount?

    A genuine arithmetic error on an invoice is small - a rounding difference, a
    mistyped digit. Being out by an order of magnitude means the columns were
    read wrong, not that the document disagrees with itself, so such a row is
    dropped rather than reported as a discrepancy the document does not contain.
    """
    product = quantity * unit_price
    if product <= 0 or amount <= 0:
        return False
    return 0.1 <= product / amount <= 10


def _resolve_columns(numbers: list[float]) -> tuple[float, float, float] | None:
    """Pick (quantity, unit_price, amount) out of a row's trailing numbers.

    The printed amount is the rightmost number. Quantity and unit price are
    normally the two before it, but invoice tables interleave item codes, unit
    codes and cost columns, so when the natural reading does not multiply out we
    look for the pair that does before giving up.
    """
    amount = numbers[-1]
    candidates = numbers[:-1]
    if len(candidates) < 2:
        return None

    natural = (candidates[0], candidates[1], amount)
    if _coheres(*natural):
        return natural

    for index in range(len(candidates) - 1):
        pair = (candidates[index], candidates[index + 1], amount)
        if pair[0] > 0 and _coheres(*pair):
            return pair

    # Nothing multiplies out. Keep the natural reading only if it is at least
    # the right order of magnitude - that is a discrepancy worth reporting;
    # anything wider is a misread table.
    return natural if _plausible_columns(*natural) else None


def _parse_table_line_item(line: str) -> tuple[str, float, float, float] | None:
    """Read an invoice table row: [item no] description qty unit_price [cost] amount.

    Written against the row rather than as one regex because invoice tables vary
    in how many money columns they print, and their rows often open with an item
    number that is not part of the description.
    """
    tokens = line.split()
    trailing: list[str] = []
    unit_token_budget = 1  # a "U/M" column ("KIT", "EA", "PCS") sits between price and amount
    while tokens:
        token = tokens[-1]
        if _NUMBER_TOKEN_RE.fullmatch(token) or _CURRENCY_ONLY_RE.match(token):
            tokens.pop()
            if not _CURRENCY_ONLY_RE.match(token):
                trailing.insert(0, token)
            continue
        if trailing and unit_token_budget and re.fullmatch(r"[A-Za-z]{1,4}", token):
            tokens.pop()
            unit_token_budget -= 1
            continue
        break
    if len(trailing) < 3:
        return None

    description = " ".join(tokens).strip(" .:|")
    head, _, rest = description.partition(" ")
    if head.isdigit() and rest:  # leading item number
        description = rest

    numbers = [value for value in (_parse_money(token) for token in trailing) if value is not None]
    if len(numbers) < 3:
        return None
    resolved = _resolve_columns(numbers)
    if resolved is None:
        return None
    quantity, unit_price, amount = resolved
    if quantity <= 0 or amount <= 0:
        return None

    # A row whose description is an item code rather than words is still a real
    # line - but only trust it when the numbers multiply out.
    if len(re.sub(r"[^A-Za-z]", "", description)) < 3 and not _coheres(quantity, unit_price, amount):
        return None
    if not description:
        return None
    return description, quantity, unit_price, amount


def _parse_money(token: str) -> float | None:
    token = re.sub(_CURRENCY, "", token.strip(), flags=re.IGNORECASE).strip()
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
    # True when a row of the item table could not be read, so the captured items
    # are known not to be the whole table.
    line_items_incomplete: bool = False
    evidence: dict[str, tuple[int, str]] = field(default_factory=dict)

    def record(self, key: str, page_number: int, line: str) -> None:
        self.evidence[key] = (page_number, line)


_TABLE_END_RE = re.compile(
    r"sub\s*total|^\s*total\b|grand\s*total|amount\s+due|balance\s+due|\bs\.?\s*total\b",
    re.IGNORECASE,
)


def _item_table_region(lines: list[str]) -> tuple[int, int] | None:
    """Rows between the item table's column header and the totals block.

    Without this bound the row reader happily turns a letterhead ("Tel. 416 431
    0440") into a line item. Receipts that print no column header get no table
    region at all; their items are picked up by the tighter receipt patterns.
    """
    for index, line in enumerate(lines):
        lowered = line.lower()
        if sum(word in lowered for word in _COLUMN_HEADER_WORDS) < 2:
            continue
        if _MONEY_RE.search(line):  # a row of figures, not a header
            continue
        end = len(lines)
        for offset in range(index + 1, len(lines)):
            if _TABLE_END_RE.search(lines[offset]):
                end = offset
                break
        return index + 1, end
    return None


def parse_invoice(lines_with_pages: list[tuple[int, str]]) -> InvoiceContext:
    """Best-effort field extraction for invoices and point-of-sale receipts.

    Every value is taken verbatim from an OCR line; nothing is inferred when a
    line is absent, so unreadable fields stay null.
    """
    ctx = InvoiceContext()
    previous_description: str | None = None
    lines = [line for _, line in lines_with_pages]
    full_text = "\n".join(lines)
    table_region = _item_table_region(lines)

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
                ctx.tax_rate_percent = float(rate_match.group(1) or rate_match.group(2))
                ctx.record("tax_rate_percent", page_number, line)

        # A line mentioning "total" is normally the total, not the tax - unless it
        # leads with a tax keyword ("GST @6% included in total RM 0.35").
        if ctx.tax_amount is None and ("total" not in lowered or _TAX_LINE_LEAD_RE.match(line)):
            tax_match = _TAX_ONLY_LINE_RE.match(line) or _TAX_AMOUNT_INLINE_RE.search(line)
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
        elif (
            "total" in lowered
            and not is_subtotal_line
            and not _TAX_LINE_LEAD_RE.match(line)
            and not any(token in lowered for token in _TOTAL_EXCLUSIONS)
        ):
            value = _last_money(line)
            if value is not None:
                ctx.total_amount = value
                ctx.record("total_amount", page_number, line)
                if re.search(r"\bincl\w*", lowered):
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

        in_table = table_region is not None and table_region[0] <= row_index < table_region[1]
        if in_table and "total" not in lowered:
            table_item = _parse_table_line_item(line)
            if table_item is None and _MONEY_RE.search(line):
                # A row of figures inside the item table that we could not read.
                # The item list is therefore incomplete, and summing it proves
                # nothing about the invoice.
                ctx.line_items_incomplete = True
            if table_item:
                description, quantity, unit_price, amount = table_item
                ctx.line_items.append(
                    InvoiceLineItem(
                        description=description,
                        quantity=quantity,
                        unit_price=unit_price,
                        amount=amount,
                        page_number=page_number,
                        source_text=line,
                    )
                )
                previous_description = None
                continue

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
