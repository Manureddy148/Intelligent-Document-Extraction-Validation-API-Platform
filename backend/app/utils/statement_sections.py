import re
from dataclasses import dataclass

from app.utils.layout import LayoutRow, split_row
from app.utils.text_parsing import StatementLineItem, StatementSection, slugify


# A heading is a line that is *only* the heading word, allowing for the roman
# numerals OCR mangles into letters ("III PROFIT" -> "Ill", "II" -> "i!").
# Matching the word anywhere in the line instead would make the document title
# "Consolidated Profit and Loss Account" open the PROFIT section on line one.
_LEAD = r"^(?:[^A-Za-z]|[IiVvXxLl]){0,6}"
_END = r"\s*:?\s*$"


@dataclass(frozen=True)
class SectionSpec:
    """How to recognise the start of one section of a statement.

    A section opens on its heading *or* on the first line item that belongs to
    it. Headings are single short words that OCR mangles freely ("EXPENDITURE"
    read as "i! EXPENDITURE", "III PROFIT" as "Il"), so relying on them alone
    silently loses every row underneath. The anchors are the distinctive line
    labels that only occur in that section, which survive OCR far better.
    """

    name: str
    heading: str
    anchors: tuple[str, ...]


BALANCE_SHEET_SPEC = (
    SectionSpec(
        "capital_and_liabilities",
        _LEAD + r"CAPITAL\s*(?:AND|&)\s*LIABILITIES" + _END,
        ("capital", "reserves and surplus", "deposits", "borrowings", "other liabilities"),
    ),
    SectionSpec(
        "assets",
        _LEAD + r"ASSETS" + _END,
        ("cash and balances", "balances with banks", "investments", "advances", "fixed assets", "other assets"),
    ),
)
BALANCE_SHEET_STOP_MARKERS = (r"Contingent liabilities", r"Bills for collection", r"Significant accounting")

PROFIT_AND_LOSS_SPEC = (
    SectionSpec("income", _LEAD + r"INCOME" + _END, ("interest earned", "other income")),
    SectionSpec(
        "expenditure",
        _LEAD + r"EXPENDITURE" + _END,
        ("interest expended", "operating expenses", "provisions and contingencies", "provisions & contingencies"),
    ),
    SectionSpec(
        "profit",
        _LEAD + r"PROFIT" + _END,
        ("net profit for the year", "minority interest", "consolidated profit", "brought forward", "share in profit"),
    ),
    SectionSpec(
        "appropriations",
        _LEAD + r"APPROPRIATIONS?" + _END,
        (
            "transfer to statutory reserve",
            "transfer to general reserve",
            "transfer to capital reserve",
            "proposed dividend",
            "interim dividend",
        ),
    ),
)
PROFIT_AND_LOSS_STOP_MARKERS = (r"EARNINGS PER", r"Significant accounting", r"As per our report")

CASH_FLOW_SPEC = (
    SectionSpec(
        "operating",
        r"^[^A-Za-z]{0,4}Cash\s+flows?\b.*\boperating activities" + _END,
        ("profit before income tax", "profit before tax", "depreciation on fixed assets"),
    ),
    SectionSpec(
        "investing",
        r"^[^A-Za-z]{0,4}Cash\s+flows?\b.*\binvesting activities" + _END,
        ("purchase of fixed assets", "proceeds from sale of fixed assets"),
    ),
    SectionSpec(
        "financing",
        r"^[^A-Za-z]{0,4}Cash\s+flows?\b.*\bfinancing activities" + _END,
        (
            "increase in minority interest",
            "money received on exercise",
            "proceeds from issue",
            "redemption of subordinated",
            "redemption of tier",
            "dividend paid during the year",
        ),
    ),
)
CASH_FLOW_STOP_MARKERS = (r"As per our report", r"Significant accounting")


def scan_sections(
    rows: list[LayoutRow],
    specs: tuple[SectionSpec, ...],
    stop_markers: tuple[str, ...],
    periods: list[str],
    page_anchors: dict[int, list[float]],
) -> dict[str, StatementSection]:
    """Walk the document in reading order, collecting line items into sections.

    Sections only ever advance, never go back, which is how these statements are
    laid out and what makes anchor matching safe.
    """
    sections: dict[str, StatementSection] = {}
    current: int | None = None
    stopped = False
    used_keys: set[str] = set()

    for row in rows:
        if stopped:
            break

        line = row.text
        parsed = split_row(row, page_anchors.get(row.page_number, []))

        if any(re.search(pattern, line, re.IGNORECASE) for pattern in stop_markers):
            stopped = True
            continue

        if parsed is None:
            for index in range(current if current is not None else 0, len(specs)):
                if re.search(specs[index].heading, line, re.IGNORECASE):
                    current = index
                    break
            continue

        label, column_values = parsed
        lowered = label.lower()
        for index in range(current if current is not None else 0, len(specs)):
            if any(anchor in lowered for anchor in specs[index].anchors):
                current = index
                break

        if current is None:
            continue

        section = sections.setdefault(specs[current].name, StatementSection(name=specs[current].name))

        base_key = f"{section.name}__{slugify(label)}"
        key = base_key
        suffix = 2
        while key in used_keys:
            key = f"{base_key}_{suffix}"
            suffix += 1
        used_keys.add(key)

        section.items.append(
            StatementLineItem(
                label=label,
                key=key,
                values={period: column_values.get(i) for i, period in enumerate(periods)},
                page_number=row.page_number,
                source_text=line,
            )
        )

    return sections


def find_in_sections(
    sections: dict[str, StatementSection],
    section_name: str,
    *keywords: str,
    exclude: tuple[str, ...] = (),
) -> StatementLineItem | None:
    """Look for a line item inside one section only.

    Used for labels that are ambiguous on their own - a statement has several
    rows labelled just "Total", and only the enclosing section says which is which.
    """
    section = sections.get(section_name)
    if not section:
        return None
    return section.find(*keywords, exclude=exclude)


def find_anywhere(
    sections: dict[str, StatementSection], *keywords: str, exclude: tuple[str, ...] = ()
) -> StatementLineItem | None:
    for section in sections.values():
        found = section.find(*keywords, exclude=exclude)
        if found:
            return found
    return None


def find_preferring_section(
    sections: dict[str, StatementSection],
    section_name: str,
    *keywords: str,
    exclude: tuple[str, ...] = (),
) -> StatementLineItem | None:
    """Look in the expected section first, then anywhere. Self-describing labels only."""
    return find_in_sections(sections, section_name, *keywords, exclude=exclude) or find_anywhere(
        sections, *keywords, exclude=exclude
    )
