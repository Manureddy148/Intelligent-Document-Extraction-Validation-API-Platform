import re

from app.utils.text_parsing import (
    StatementLineItem,
    StatementSection,
    resolve_period_values,
    slugify,
    split_label_and_numbers,
)

SectionMarkers = list[tuple[str, str]]

BALANCE_SHEET_SECTION_MARKERS: SectionMarkers = [
    ("capital_and_liabilities", r"CAPITAL\s+AND\s+LIABILITIES"),
    ("assets", r"^\s*ASSETS\s*$"),
]
BALANCE_SHEET_STOP_MARKERS = [r"Contingent liabilities", r"Bills for collection", r"Significant accounting"]

PROFIT_AND_LOSS_SECTION_MARKERS: SectionMarkers = [
    ("income", r"\bINCOME\b"),
    ("expenditure", r"\bEXPENDITURE\b"),
    ("profit", r"^\s*I{1,3}\s+PROFIT\b|^\s*PROFIT\b"),
    ("appropriations", r"APPROPRIATIONS"),
]
PROFIT_AND_LOSS_STOP_MARKERS = [r"EARNINGS PER", r"Significant accounting", r"As per our report"]

CASH_FLOW_SECTION_MARKERS: SectionMarkers = [
    ("operating", r"operating activities"),
    ("investing", r"investing activities"),
    ("financing", r"financing activities"),
]
CASH_FLOW_STOP_MARKERS = [r"As per our report", r"Significant accounting"]


def scan_sections(
    lines_with_pages: list[tuple[int, str]],
    section_markers: SectionMarkers,
    stop_markers: list[str],
    periods: list[str],
) -> dict[str, StatementSection]:
    sections: dict[str, StatementSection] = {}
    current: StatementSection | None = None
    used_keys: set[str] = set()

    for page_number, line in lines_with_pages:
        if any(re.search(pattern, line, re.IGNORECASE) for pattern in stop_markers):
            current = None
            continue

        matched_section = False
        for key, pattern in section_markers:
            if re.search(pattern, line, re.IGNORECASE):
                current = sections.setdefault(key, StatementSection(name=key))
                matched_section = True
                break
        if matched_section:
            continue

        if current is None:
            continue

        parsed = split_label_and_numbers(line)
        if not parsed:
            continue

        values = resolve_period_values(parsed)
        period_values = {
            periods[i]: (values[i] if i < len(values) else None) for i in range(len(periods))
        }

        base_key = f"{current.name}__{slugify(parsed.label)}"
        key = base_key
        suffix = 2
        while key in used_keys:
            key = f"{base_key}_{suffix}"
            suffix += 1
        used_keys.add(key)

        current.items.append(
            StatementLineItem(
                label=parsed.label,
                key=key,
                values=period_values,
                page_number=page_number,
                source_text=parsed.raw_text,
            )
        )
    return sections


def find_in_sections(
    sections: dict[str, "StatementSection"], section_name: str, *keywords: str
) -> StatementLineItem | None:
    section = sections.get(section_name)
    if not section:
        return None
    return section.find(*keywords)


def find_anywhere(sections: dict[str, "StatementSection"], *keywords: str) -> StatementLineItem | None:
    for section in sections.values():
        found = section.find(*keywords)
        if found:
            return found
    return None
