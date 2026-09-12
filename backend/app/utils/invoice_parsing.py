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
# The document keyword and the "No." marker are often separated by other words
# ("Invoice - Facture NO: 138236"), and OCR reads the colon as a letter
# ("INVOICE NO s 18291/102/70163"). Matched only on a line that names an
# invoice, so "No of items: 2" and "GST Reg. No." are not mistaken for one.
_INVOICE_KEYWORD_RE = re.compile(r"\b(?:invoice|facture|receipt|bill|tax\s+invoice)\b", re.IGNORECASE)
_INVOICE_NO_LOOSE_RE = re.compile(
    r"\b(?:no|num|number|#)\b\.?\s*[:;=+*\-\.s]?\s*([A-Z0-9][A-Z0-9\-/]{3,})", re.IGNORECASE
)
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
# OCR routinely reads the leading "1" of "1x" as a letter ("tx", "ix", "lx",
# "|x"), which lost the line item on several receipts in the dataset; those
# forms are read as a quantity of one.
_ONE_CONFUSIONS = r"[1lI|ti!]"
_QTY_X_PRICE_RE = re.compile(
    rf"^(?:(?P<qty>\d+(?:\.\d+)?)|{_ONE_CONFUSIONS})\s*[xX]\s*"
    r"(?P<unit_price>\d[\d,]*[.,]\d{2})"
    r"(?:\s+(?P<amount>\d[\d,]*[.,]\d{2}))?\s*[A-Za-z]{0,3}$"
)
# "#2 X RM 2,20" - quantity and unit price under a description that already
# carried the line amount.
_HASH_QTY_X_PRICE_RE = re.compile(
    rf"^[#*]?\s*(?:(?P<qty>\d+(?:\.\d+)?)|{_ONE_CONFUSIONS})\s*[xX]\s*"
    rf"(?:{_CURRENCY})?\s*(?P<unit_price>\d[\d,]*[.,]\d{{2}})\s*$",
    re.IGNORECASE,
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

# Charges added between the subtotal and the total - shipping, handling, service
# charge, rounding. Without these the total check reports a shortfall on any
# invoice that carries one. Matched only as a short label-and-amount line in the
# totals block, so an item row such as "Freight AE Blake Montreal to Aerospace
# Metal 6 136.00" is not mistaken for a shipping charge.
_ADDITIONAL_CHARGE_RE = re.compile(
    r"^\W*(?:s\s*&\s*h|shipping(?:\s*(?:&|and)\s*handling)?|handling|freight|delivery"
    r"|service\s*charge|svc\s*charge|rounding(?:\s*adj\w*)?)\b[^A-Za-z]*$",
    re.IGNORECASE,
)

# "GST @6% included in total RM 0.35" states the tax, not the total. Reading it
# as the total replaces a RM 6.20 bill with RM 0.35 and makes the change due
# look wrong by the whole value of the sale.
_TAX_LINE_LEAD_RE = re.compile(r"^\W*(?:gst|sst|hst|pst|qst|vat|tax|service\s+charge|svc)\b", re.IGNORECASE)


def _coheres(quantity: float, unit_price: float, amount: float) -> bool:
    """Does quantity * unit_price come out at the printed line amount?"""
    return abs(quantity * unit_price - amount) <= max(0.02, 0.01 * abs(amount))


def _plausible_columns(quantity: float, unit_price: float, amount: float) -> bool:
    """Is quantity * unit_price close enough to the printed amount to report on?

    A genuine arithmetic error on an invoice is small - a rounding difference, a
    mistyped digit - and is worth flagging. A wide miss means a column was read
    wrong instead: invoices print list price, net rate, discount and unit-of-
    measure columns in varying orders, and picking the wrong one is a failure of
    this parser, not a discrepancy in the document. Those rows keep their
    description and amount and report no quantity rather than a false finding.
    """
    product = quantity * unit_price
    if product <= 0 or amount <= 0:
        return False
    return 0.8 <= product / amount <= 1.25


def _resolve_columns(numbers: list[float]) -> tuple[float | None, float | None, float] | None:
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

    # Nothing multiplies out. Keep the natural reading only if it is a near
    # miss - that is a discrepancy worth reporting; anything wider means the
    # columns were misread, so the quantity and rate are left unknown.
    return natural if _plausible_columns(*natural) else (None, None, amount)


def _parse_table_line_item(line: str) -> tuple[str, float | None, float | None, float] | None:
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
    if amount <= 0 or (quantity is not None and quantity <= 0):
        return None

    # A row whose description is an item code rather than words is still a real
    # line - but only trust it when the numbers multiply out.
    coherent = None not in (quantity, unit_price) and _coheres(quantity, unit_price, amount)
    if len(re.sub(r"[^A-Za-z]", "", description)) < 3 and not coherent:
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


_TRAILING_INTEGER_RE = re.compile(rf"(?:{_CURRENCY}\s*)?(-?\d[\d,]*)\s*$", re.IGNORECASE)


def _labelled_amount(line: str) -> float | None:
    """The amount on a totals-block line, allowing a whole number.

    Invoices print round figures without decimals - "Subtotal 804", "S&H 50" -
    which the money pattern deliberately ignores, because a bare integer
    anywhere else on a receipt is far more likely to be a quantity or an item
    code. It is only read here, where the line's own label already says the
    trailing number is an amount.
    """
    value = _last_money(line)
    if value is not None:
        return value
    # Only when the line carries a single number. OCR splits decimals into two
    # groups - "TOTAL. 4. 60" is 4.60, not 60 - and there is no way to tell that
    # from a genuine whole number, so an ambiguous line is left unread.
    if len(re.findall(r"\d+", line)) != 1:
        return None
    match = _TRAILING_INTEGER_RE.search(line.strip())
    return _parse_money(match.group(1)) if match else None


@dataclass
class InvoiceLineItem:
    description: str
    # Null when the row's columns could not be resolved: the amount is printed
    # plainly, but which figure is the quantity and which the rate is not always
    # recoverable from a scan. A guess there would be an invented value.
    quantity: float | None
    unit_price: float | None
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
    additional_charges: float | None = None
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


# The party the invoice is addressed to. "Ship To"/"Deliver To" name a delivery
# address rather than the buyer, so they are only used when nothing better is
# found.
_CUSTOMER_LABEL_RE = re.compile(
    r"\b(?:sold\s*to|bill(?:ed)?\s*to|invoice\s*to|customer|client|buyer|vendu)\b", re.IGNORECASE
)
_FALLBACK_CUSTOMER_LABEL_RE = re.compile(r"\b(?:ship\s*to|deliver(?:ed)?\s*to|livr)\b", re.IGNORECASE)
# Labels that mark the *seller* column, used to bound the customer's column.
_SELLER_LABEL_RE = re.compile(r"\b(?:seller|vendor|from|supplier|remit\s*to)\b", re.IGNORECASE)
# Column headers inside a details grid ("CUSTOMER ACCOUNT", "CUSTOMER PO") name
# a field, not the buyer.
_CUSTOMER_FIELD_HEADER_RE = re.compile(
    r"\b(?:account|a/?c|po\b|p\.o\.|order|number|no\.?|ref|id|code|copy|service|care)\b", re.IGNORECASE
)


# Invoice headers pack several labelled boxes onto one line, so a name read from
# one box often runs into the label of the next ("Laxmi Narayan Bhandar 2 Terms
# of Delivery"). The name ends where the neighbouring label begins.
_ADJACENT_FIELD_LABEL_RE = re.compile(
    r"\s*\b(?:terms?\s+of\b|mode/?\s*terms?\b|delivery\s+note\b|dispatch\w*\b|destination\b"
    r"|buyer'?s?\s+order\b|reference\w*\b|due\s+date\b|invoice\s+date\b|dated\b|other\s+references?\b"
    r"|supplier'?s?\s+ref\b|\bno\.?\s*:?\s*$).*",
    re.IGNORECASE,
)


def _trim_party_name(value: str) -> str:
    """Cut a name at the neighbouring column's label and tidy the edges."""
    trimmed = _ADJACENT_FIELD_LABEL_RE.sub("", value).strip()
    # Trailing OCR crumbs from the next column ("-(7° il No.") leave stray
    # punctuation and one- or two-character fragments behind.
    trimmed = re.sub(r"[\s\-_,;:|/(\[]+$", "", trimmed)
    while True:
        shorter = re.sub(r"\s+[^A-Za-z0-9]{1,3}$", "", trimmed).strip()
        shorter = re.sub(r"\s+(?:[A-Za-z]{1,2}|\d{1,2})$", "", shorter).strip()
        if shorter == trimmed or len(re.sub(r"[^A-Za-z]", "", shorter)) < 4:
            break
        trimmed = shorter
    return re.sub(r"[\s\-_,;:|/(\[]+$", "", trimmed).strip()


def _looks_like_a_party_name(line: str) -> bool:
    """Enough letters, and not a date, an amount, or a bare address line."""
    letters = re.sub(r"[^A-Za-z]", "", line)
    if len(letters) < 4:
        return False
    if _DATE_RE.search(line) or _MONEY_RE.search(line):
        return False
    if _ADDRESS_HINT_RE.search(line):
        return False
    # Mostly-digit lines are account numbers, not names.
    return len(letters) / max(len(re.sub(r"\s", "", line)), 1) >= 0.6


def _band_text(row, x_from: float, x_to: float) -> str:
    """Words of a row whose left edge falls inside one horizontal column band."""
    return " ".join(w.text for w in row.words if x_from - 1 <= w.x0 < x_to).strip()


def _find_customer_name(lines: list[str], rows: list | None) -> tuple[str, int] | None:
    """The buyer's name, read from whichever column its label sits above.

    Invoices print "Seller:" and "Client:" side by side, which OCR flattens into
    one line ("Cruz PLC Sandoval-Phillips"). Splitting that by text alone is
    guesswork, so where word geometry is available the name is taken from the
    horizontal band under the customer label; without geometry only a
    single-label row is trusted.
    """
    for pattern in (_CUSTOMER_LABEL_RE, _FALLBACK_CUSTOMER_LABEL_RE):
        for index, line in enumerate(lines[:25]):
            found = list(pattern.finditer(line))
            if not found:
                continue
            # Take the rightmost label: "Vendu 4 - Sold To" is one label phrase,
            # and slicing after the first match would return "4 - Sold To".
            match = found[-1]

            # "Bill To: ACME Corp" - the name is on the label line itself.
            inline = line[match.end():].lstrip(" :\t-")
            if (
                _looks_like_a_party_name(inline)
                and not _CUSTOMER_FIELD_HEADER_RE.search(inline[:24])
                and not _CUSTOMER_LABEL_RE.search(inline)
                and not _FALLBACK_CUSTOMER_LABEL_RE.search(inline)
                and not _SELLER_LABEL_RE.search(inline)
            ):
                return _trim_party_name(inline), index

            # Otherwise the name is on the row below, in the label's column.
            if index + 1 >= len(lines):
                continue
            below = lines[index + 1]
            row = rows[index] if rows and index < len(rows) else None
            row_below = rows[index + 1] if rows and index + 1 < len(rows) else None

            candidate = below
            if row is not None and row_below is not None:
                label_word = next(
                    (w for w in row.words if pattern.search(w.text)),
                    None,
                )
                if label_word is not None:
                    # The customer column runs from its label to the next label
                    # to its right, whatever that label is.
                    later = [
                        w.x0
                        for w in row.words
                        if w.x0 > label_word.x0 + 1
                        and (pattern.search(w.text) or _SELLER_LABEL_RE.search(w.text)
                             or _FALLBACK_CUSTOMER_LABEL_RE.search(w.text))
                    ]
                    # Text inside a labelled box often starts a little left of the
                    # label itself, so the band opens slightly before it - scaled
                    # to the text height so it holds at any resolution - but never
                    # back past whatever label sits to its left.
                    indent = 3 * max(label_word.bottom - label_word.top, 1.0)
                    earlier = [
                        w.x1
                        for w in row.words
                        if w.x1 <= label_word.x0
                        and (pattern.search(w.text) or _SELLER_LABEL_RE.search(w.text)
                             or _FALLBACK_CUSTOMER_LABEL_RE.search(w.text))
                    ]
                    start = max(label_word.x0 - indent, max(earlier, default=0.0))
                    candidate = _band_text(row_below, start, min(later) if later else float("inf")) or below

            if _looks_like_a_party_name(candidate):
                return _trim_party_name(candidate), index + 1
    return None


def parse_invoice(lines_with_pages: list[tuple[int, str]], rows: list | None = None) -> InvoiceContext:
    """Best-effort field extraction for invoices and point-of-sale receipts.

    Every value is taken verbatim from an OCR line; nothing is inferred when a
    line is absent, so unreadable fields stay null.
    """
    ctx = InvoiceContext()
    previous_description: str | None = None
    previous_amount: float | None = None
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
            ctx.vendor_name = _trim_party_name(line)
            ctx.record("vendor_name", page_number, line)
        elif ctx.vendor_address is None and _ADDRESS_HINT_RE.search(line):
            ctx.vendor_address = line
            ctx.record("vendor_address", page_number, line)

        if ctx.vendor_tax_id is None:
            tax_id = _TAX_ID_RE.search(line)
            if tax_id and not _MONEY_RE.search(line) and _HAS_DIGIT_RE.search(tax_id.group(1)):
                ctx.vendor_tax_id = tax_id.group(1)
                ctx.record("vendor_tax_id", page_number, line)

        if ctx.invoice_number is None and not _MONEY_RE.search(line):
            invoice_no = _INVOICE_NO_RE.search(line)
            if invoice_no is None and _INVOICE_KEYWORD_RE.search(line):
                invoice_no = _INVOICE_NO_LOOSE_RE.search(line)
            if invoice_no and _HAS_DIGIT_RE.search(invoice_no.group(1)):
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
            value = _labelled_amount(line)
            if value is not None:
                ctx.subtotal = value
                ctx.record("subtotal", page_number, line)

        if ctx.additional_charges is None:
            label = _ADDITIONAL_CHARGE_RE.match(line)
            if label:
                value = _labelled_amount(line)
                if value is not None and value > 0:
                    ctx.additional_charges = value
                    ctx.record("additional_charges", page_number, line)

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
            value = _labelled_amount(line)
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

        # Receipts split an item across two rows: the description on one, the
        # "1x 12.50 12.50" figures on the next. The description row sometimes
        # already carries the line amount ("COKE LIGHT RM4.40" / "#2 X RM 2.20").
        qty_x_match = _QTY_X_PRICE_RE.match(line) or _HASH_QTY_X_PRICE_RE.match(line)
        if qty_x_match and previous_description:
            unit_price = _parse_money(qty_x_match.group("unit_price"))
            groups = qty_x_match.groupdict()
            # An unmatched quantity group is the OCR-confusion branch ("tx 2.64"),
            # which is a quantity of one.
            quantity = float(groups["qty"]) if groups.get("qty") else 1.0
            amount = _parse_money(groups["amount"]) if groups.get("amount") else None
            if amount is None:
                amount = previous_amount
            if amount is None and unit_price is not None:
                amount = round(quantity * unit_price, 2)
            if unit_price is not None and amount is not None:
                ctx.line_items.append(
                    InvoiceLineItem(
                        description=previous_description,
                        quantity=quantity,
                        unit_price=unit_price,
                        amount=amount,
                        page_number=page_number,
                        source_text=f"{previous_description} / {line}",
                    )
                )
            previous_description, previous_amount = None, None
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
            letters = len(re.sub(r"[^A-Za-z]", "", line))
            money = _money_values(line)
            if letters >= 3 and not money:
                previous_description, previous_amount = line, None
            elif letters >= 3 and len(money) == 1:
                # "973 COKE LIGHT 500ML RM4.40" - a description that already
                # carries its line amount; the quantity follows on the next row.
                previous_description, previous_amount = line, money[0]
            else:
                previous_description, previous_amount = None, None

    customer = _find_customer_name(lines, rows)
    if customer:
        name, row_index = customer
        ctx.customer_name = name
        ctx.record("customer_name", lines_with_pages[row_index][0], lines[row_index])

    # Without a column header there is no bounded item table, so the rows that
    # matched a line-item shape are whatever the heuristics happened to catch -
    # there is no way to tell a complete list from a partial one. Summing that
    # against a printed subtotal would report a shortfall belonging to the
    # reading. Rows found inside a real table region stay reconcilable.
    if ctx.line_items and table_region is None:
        ctx.line_items_incomplete = True

    if ctx.tax_inclusive is None and ctx.tax_amount is not None and ctx.subtotal is None:
        ctx.tax_inclusive = True

    for pattern, code in ((r"\bRM\b|\bMYR\b", "MYR"), (r"\bUSD\b|\$", "USD"), (r"\bINR\b|₹", "INR"), (r"\bEUR\b|€", "EUR")):
        if re.search(pattern, full_text):
            ctx.currency = code
            break

    return ctx
