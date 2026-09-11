import re
from dataclasses import dataclass, field


def slugify(label: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", label.strip().lower())
    return text.strip("_") or "field"


# A thousands separator always leaves a group of three digits behind it, so a
# trailing group of exactly two is a decimal point OCR read as a comma:
# "554,55" is 554.55, not 55455. Getting this wrong inflates a figure a
# hundredfold and makes a balance sheet that reconciles look like it does not.
_COMMA_DECIMAL_RE = re.compile(r"^(\d{1,3}(?:,\d{3})*),(\d{2})$")


def parse_number(token: str) -> float | None:
    """Parse an accounting-style number: '(1,234)' -> -1234.0, '-' -> None."""
    token = token.strip()
    if not token or token in {"-", "--", "—", "–"}:
        return None
    negative = token.startswith("(") and token.endswith(")")
    cleaned = token.strip("()")
    comma_decimal = _COMMA_DECIMAL_RE.match(cleaned)
    if comma_decimal:
        cleaned = f"{comma_decimal.group(1)}.{comma_decimal.group(2)}"
    cleaned = cleaned.replace(",", "")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return -value if negative else value


@dataclass
class StatementLineItem:
    label: str
    key: str
    values: dict[str, float | None]
    page_number: int
    source_text: str


@dataclass
class StatementSection:
    name: str
    items: list[StatementLineItem] = field(default_factory=list)

    def find(self, *keywords: str, exclude: tuple[str, ...] = ()) -> StatementLineItem | None:
        """First item whose label contains any keyword and none of `exclude`.

        Exclusions matter because these statements reuse a phrase across rows
        that mean different things: "Net Profit *before* Minority Interest" is
        the profit row, not the minority interest row, and matching it would
        make the two fields report the same number.
        """
        for item in self.items:
            lowered = item.label.lower()
            if any(term.lower() in lowered for term in exclude):
                continue
            if any(keyword.lower() in lowered for keyword in keywords):
                return item
        return None
