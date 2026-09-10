import re
from dataclasses import dataclass, field

_NUMBER_TOKEN_RE = re.compile(r"^\(?-?[\d,]+(?:\.\d+)?\)?$")
_TRAILING_NUMBERS_RE = re.compile(r"^(?P<label>.*?)(?P<nums>(?:\s+\(?-?[\d][\d,]*(?:\.\d+)?\)?){1,4})\s*$")
_DATE_TOKEN_RE = re.compile(r"\b\d{1,2}[-/][A-Za-z]{3,9}[-/]\d{2,4}\b")


def slugify(label: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", label.strip().lower())
    return text.strip("_") or "field"


def parse_number(token: str) -> float | None:
    """Parse an accounting-style number token: '(1,234)' -> -1234.0, '-' -> None."""
    token = token.strip()
    if not token or token in {"-", "--", "—"}:
        return None
    negative = token.startswith("(") and token.endswith(")")
    cleaned = token.strip("()").replace(",", "")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return -value if negative else value


@dataclass
class ParsedLine:
    label: str
    numbers: list[float | None]
    raw_text: str


def split_label_and_numbers(line: str) -> ParsedLine | None:
    """Split a line of a tabular financial statement into a label and trailing numeric columns."""
    match = _TRAILING_NUMBERS_RE.match(line.strip())
    if not match:
        return None
    label = match.group("label").strip(" .")
    raw_numbers = match.group("nums").split()
    numbers = [parse_number(tok) for tok in raw_numbers if _NUMBER_TOKEN_RE.match(tok)]
    if not label or not numbers:
        return None
    return ParsedLine(label=label, numbers=numbers, raw_text=line.strip())


def resolve_period_values(parsed: ParsedLine) -> list[float | None]:
    """Drop a leading schedule/note-reference number when 3+ numeric columns are present."""
    numbers = parsed.numbers
    if len(numbers) >= 3 and numbers[0] is not None and abs(numbers[0]) < 100 and numbers[0] == int(numbers[0]):
        return numbers[1:]
    return numbers


def extract_period_labels(lines: list[str], max_lines_scanned: int = 20) -> list[str]:
    for line in lines[:max_lines_scanned]:
        matches = _DATE_TOKEN_RE.findall(line)
        if len(matches) >= 2:
            return matches
    return ["period_1", "period_2"]


def find_first_date(text: str) -> str | None:
    match = _DATE_TOKEN_RE.search(text)
    return match.group(0) if match else None


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

    def find(self, *keywords: str) -> StatementLineItem | None:
        for item in self.items:
            lowered = item.label.lower()
            if any(keyword.lower() in lowered for keyword in keywords):
                return item
        return None
