import re
from dataclasses import dataclass, field


def slugify(label: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", label.strip().lower())
    return text.strip("_") or "field"


def parse_number(token: str) -> float | None:
    """Parse an accounting-style number: '(1,234)' -> -1234.0, '-' -> None."""
    token = token.strip()
    if not token or token in {"-", "--", "—", "–"}:
        return None
    negative = token.startswith("(") and token.endswith(")")
    cleaned = token.strip("()").replace(",", "")
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

    def find(self, *keywords: str) -> StatementLineItem | None:
        for item in self.items:
            lowered = item.label.lower()
            if any(keyword.lower() in lowered for keyword in keywords):
                return item
        return None
