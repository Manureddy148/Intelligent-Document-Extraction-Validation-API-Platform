import re

from app.utils.layout import LayoutRow, split_row
from app.utils.text_parsing import StatementLineItem, StatementSection, slugify

SectionMarkers = list[tuple[str, str]]

# Roman numerals in these statements are frequently misread by OCR
# ("III" -> "Ill", "II" -> "Il", "I" -> "]"), so section headings are matched
# with a tolerant leading-noise prefix.
_HEADING_PREFIX = r"^[\sIVXil|\]\)\.\d]{0,6}"

BALANCE_SHEET_SECTION_MARKERS: SectionMarkers = [
    ("capital_and_liabilities", r"CAPITAL\s+AND\s+LIABILITIES"),
    ("assets", _HEADING_PREFIX + r"ASSETS\s*$"),
]
BALANCE_SHEET_STOP_MARKERS = [r"Contingent liabilities", r"Bills for collection", r"Significant accounting"]

PROFIT_AND_LOSS_SECTION_MARKERS: SectionMarkers = [
    ("income", _HEADING_PREFIX + r"INCOME\b"),
    ("expenditure", _HEADING_PREFIX + r"EXPENDITURE\b"),
    ("profit", _HEADING_PREFIX + r"PROFIT\b"),
    ("appropriations", _HEADING_PREFIX + r"APPROPRIATIONS\b"),
]
PROFIT_AND_LOSS_STOP_MARKERS = [r"EARNINGS PER", r"Significant accounting", r"As per our report"]

CASH_FLOW_SECTION_MARKERS: SectionMarkers = [
    ("operating", r"Cash flows? (?:from|used in).{0,20}operating activities"),
    ("investing", r"Cash flows? (?:from|used in).{0,20}investing activities"),
    ("financing", r"Cash flows? (?:from|used in).{0,20}financing activities"),
]
CASH_FLOW_STOP_MARKERS = [r"As per our report", r"Significant accounting"]


def scan_sections(
    rows: list[LayoutRow],
    section_markers: SectionMarkers,
    stop_markers: list[str],
    periods: list[str],
    page_anchors: dict[int, list[float]],
) -> dict[str, StatementSection]:
    """Walk the document rows, tracking the current section and collecting line items.

    A row that carries values is always a line item - never a heading - so
    labels that repeat a section keyword (e.g. "Other income", "Net cash flow
    from operating activities") are captured rather than swallowed as headings.
    """
    sections: dict[str, StatementSection] = {}
    current: StatementSection | None = None
    used_keys: set[str] = set()

    for row in rows:
        line = row.text
        parsed = split_row(row, page_anchors.get(row.page_number, []))

        if parsed is None:
            if any(re.search(pattern, line, re.IGNORECASE) for pattern in stop_markers):
                current = None
                continue
            for key, pattern in section_markers:
                if re.search(pattern, line, re.IGNORECASE):
                    current = sections.setdefault(key, StatementSection(name=key))
                    break
            continue

        if any(re.search(pattern, line, re.IGNORECASE) for pattern in stop_markers):
            current = None
            continue

        if current is None:
            continue

        label, column_values = parsed
        period_values = {
            period: column_values.get(index) for index, period in enumerate(periods)
        }

        base_key = f"{current.name}__{slugify(label)}"
        key = base_key
        suffix = 2
        while key in used_keys:
            key = f"{base_key}_{suffix}"
            suffix += 1
        used_keys.add(key)

        current.items.append(
            StatementLineItem(
                label=label,
                key=key,
                values=period_values,
                page_number=row.page_number,
                source_text=line,
            )
        )
    return sections


def find_in_sections(
    sections: dict[str, StatementSection], section_name: str, *keywords: str
) -> StatementLineItem | None:
    """Look for a line item inside one section only.

    Used for labels that are ambiguous on their own - a statement has several
    rows labelled just "Total", and only the enclosing section says which is which.
    """
    section = sections.get(section_name)
    if not section:
        return None
    return section.find(*keywords)


def find_anywhere(sections: dict[str, StatementSection], *keywords: str) -> StatementLineItem | None:
    for section in sections.values():
        found = section.find(*keywords)
        if found:
            return found
    return None


def find_preferring_section(
    sections: dict[str, StatementSection], section_name: str, *keywords: str
) -> StatementLineItem | None:
    """Look in the expected section first, then anywhere in the document.

    Section headings are single short words that OCR sometimes drops entirely
    (e.g. "III PROFIT" read as "Il"), which would otherwise lose every line item
    beneath them. Only self-describing labels should use this.
    """
    return find_in_sections(sections, section_name, *keywords) or find_anywhere(sections, *keywords)
