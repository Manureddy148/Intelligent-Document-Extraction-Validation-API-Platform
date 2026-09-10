"""Layout-aware row/column reconstruction for tabular documents.

Financial statements are tables: a label on the left and one value per period
column, right-aligned. Plain OCR text loses that geometry - when a row is only
populated for one period, a text-only parser cannot tell which period the lone
value belongs to, and some OCR page-segmentation modes emit labels and numbers
as separate blocks entirely.

This module keeps the word bounding boxes, rebuilds rows by vertical position
and assigns every number to a period column by horizontal position.
"""

import re
from dataclasses import dataclass, field

from app.utils.text_parsing import parse_number

_NUMERIC_TOKEN_RE = re.compile(r"^\(?-?[\d][\d,]*(?:\.\d+)?\)?[-,.]?$")
# "31-Mar-17" (older statements) and "March 31, 2024" (newer ones).
_DATE_TOKEN_RE = re.compile(r"^\d{1,2}[-/][A-Za-z]{3,9}[-/]\d{2,4}$")
_MONTH_NAME_RE = re.compile(
    r"^(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*$", re.IGNORECASE
)
_YEAR_RE = re.compile(r"^(19|20)\d{2}$")


@dataclass
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float

    @property
    def x_center(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def y_center(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def height(self) -> float:
        return max(self.bottom - self.top, 1.0)

    def is_numeric(self) -> bool:
        return bool(_NUMERIC_TOKEN_RE.match(self.text)) and parse_number(self.text.rstrip(",.")) is not None

    def is_date(self) -> bool:
        return bool(_DATE_TOKEN_RE.match(self.text))


@dataclass
class LayoutRow:
    page_number: int
    words: list[Word] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words).strip()

    @property
    def y_center(self) -> float:
        return sum(word.y_center for word in self.words) / len(self.words)

    def numeric_words(self) -> list[Word]:
        return [word for word in self.words if word.is_numeric()]

    def date_words(self) -> list[Word]:
        return [word for word in self.words if word.is_date()]


def _merge_bracket_tokens(words: list[Word]) -> list[Word]:
    """Rejoin numbers OCR split around brackets, e.g. '(', '87,543)' -> '(87,543)'."""
    merged: list[Word] = []
    for word in words:
        if merged and (
            (merged[-1].text == "(" and word.text.endswith(")"))
            or (merged[-1].text.startswith("(") and not merged[-1].text.endswith(")") and word.text == ")")
        ):
            previous = merged.pop()
            merged.append(
                Word(
                    text=previous.text + word.text,
                    x0=previous.x0,
                    x1=word.x1,
                    top=min(previous.top, word.top),
                    bottom=max(previous.bottom, word.bottom),
                )
            )
        else:
            merged.append(word)
    return merged


def group_words_into_rows(words: list[Word], page_number: int) -> list[LayoutRow]:
    """Cluster words into visual rows by vertical overlap, then sort left to right."""
    usable = [w for w in words if w.text.strip()]
    if not usable:
        return []

    median_height = sorted(w.height for w in usable)[len(usable) // 2]
    tolerance = max(median_height * 0.6, 3.0)

    rows: list[list[Word]] = []
    for word in sorted(usable, key=lambda w: w.y_center):
        for row in reversed(rows):
            if abs(row[0].y_center - word.y_center) <= tolerance:
                row.append(word)
                break
        else:
            rows.append([word])

    layout_rows = []
    for row in rows:
        ordered = _merge_bracket_tokens(sorted(row, key=lambda w: w.x0))
        layout_rows.append(LayoutRow(page_number=page_number, words=ordered))
    return sorted(layout_rows, key=lambda r: r.y_center)


def _named_period_labels(row: LayoutRow) -> list[str]:
    """Rebuild 'March 31, 2024'-style period headings from their separate words."""
    labels: list[str] = []
    words = row.words
    for index, word in enumerate(words):
        if not _MONTH_NAME_RE.match(word.text):
            continue
        parts = [word.text]
        for following in words[index + 1 : index + 3]:
            parts.append(following.text)
            if _YEAR_RE.match(following.text):
                labels.append(" ".join(parts))
                break
    return labels


def _is_period_header_row(row: LayoutRow) -> bool:
    if len(row.date_words()) >= 2:
        return True
    return sum(1 for word in row.words if _MONTH_NAME_RE.match(word.text)) >= 2


def detect_period_columns(rows: list[LayoutRow]) -> tuple[list[str], list[float]]:
    """Find period labels and the x-anchor of each value column.

    Column anchors come from the right edges of the numeric tokens (financial
    tables right-align values), so a row populated for only one period still
    lands in the correct column.
    """
    period_labels: list[str] = []
    for row in rows:
        dates = row.date_words()
        if len(dates) >= 2:
            period_labels = [word.text for word in sorted(dates, key=lambda w: w.x0)]
            break
        named = _named_period_labels(row)
        if len(named) >= 2:
            period_labels = named
            break

    # The period header itself contains numbers ("31,", "2024"); excluding those
    # rows keeps them out of the value-column clustering.
    right_edges = sorted(
        word.x1 for row in rows if not _is_period_header_row(row) for word in row.numeric_words()
    )
    if not right_edges:
        return period_labels or ["period_1", "period_2"], []

    page_width = max(right_edges)
    cluster_tolerance = max(page_width * 0.02, 6.0)

    clusters: list[list[float]] = []
    for edge in right_edges:
        if clusters and edge - clusters[-1][-1] <= cluster_tolerance:
            clusters[-1].append(edge)
        else:
            clusters.append([edge])

    expected_columns = len(period_labels) if period_labels else 2
    # Prefer the best-supported columns, and on a tie the rightmost ones: value
    # columns sit to the right of any schedule/note-reference column.
    ranked = sorted(clusters, key=lambda c: (len(c), sum(c) / len(c)), reverse=True)[:expected_columns]
    anchors = sorted(sum(cluster) / len(cluster) for cluster in ranked)

    if not period_labels:
        period_labels = [f"period_{index + 1}" for index in range(len(anchors))]
    elif len(anchors) < len(period_labels):
        period_labels = period_labels[: len(anchors)]

    return period_labels, anchors


def split_row(row: LayoutRow, anchors: list[float]) -> tuple[str, dict[int, float]] | None:
    """Split a row into its label and {column_index: value} using the column anchors.

    Numbers that do not sit near a value column (schedule/note references) are
    excluded from the values and dropped from the label.
    """
    if not anchors:
        return None

    if len(anchors) > 1:
        gaps = [anchors[i + 1] - anchors[i] for i in range(len(anchors) - 1)]
        threshold = min(gaps) * 0.5
    else:
        threshold = max(anchors[0] * 0.08, 20.0)

    values: dict[int, float] = {}
    label_words: list[str] = []
    first_value_x0: float | None = None

    for word in row.words:
        if word.is_numeric():
            distances = [abs(word.x1 - anchor) for anchor in anchors]
            best = min(range(len(anchors)), key=lambda i: distances[i])
            if distances[best] <= threshold:
                value = parse_number(word.text.rstrip(",."))
                if value is not None and best not in values:
                    values[best] = value
                    if first_value_x0 is None or word.x0 < first_value_x0:
                        first_value_x0 = word.x0
                continue
        label_words.append(word.text)

    if first_value_x0 is not None:
        label_words = [w.text for w in row.words if w.x1 <= first_value_x0 and not w.is_numeric()]

    label = " ".join(label_words).strip(" .:")
    if not label or not values:
        return None
    return label, values
