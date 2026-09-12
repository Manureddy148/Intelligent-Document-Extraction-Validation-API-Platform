import re
from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.schemas.extraction import DocumentType
from app.services.ocr_service import PageText
from app.utils.invoice_parsing import InvoiceContext, parse_invoice
from app.utils.layout import LayoutRow, detect_period_columns
from app.utils.statement_sections import (
    BALANCE_SHEET_SPEC,
    BALANCE_SHEET_STOP_MARKERS,
    CASH_FLOW_SPEC,
    CASH_FLOW_STOP_MARKERS,
    PROFIT_AND_LOSS_SPEC,
    PROFIT_AND_LOSS_STOP_MARKERS,
    find_in_sections,
    find_preferring_section,
    scan_sections,
)
from app.utils.text_parsing import StatementLineItem, StatementSection

logger = get_logger(__name__)

_UNIT_RE = re.compile(r"in\s*['’`]?\s*(000|00,000|lakhs?|crores?|millions?|billions?)", re.IGNORECASE)
_CURRENCY_HINTS = [
    (r"₹|\bINR\b|\bRs\.?\b", "INR"),
    (r"\bUSD\b|\$", "USD"),
    (r"\bEUR\b|€", "EUR"),
    (r"\bGBP\b|£", "GBP"),
    (r"\bRM\b|\bMYR\b", "MYR"),
]


def _detect_currency_and_unit(lines: list[str]) -> tuple[str | None, str | None, str | None]:
    """Detect reporting currency and unit multiplier from the statement header."""
    header = "\n".join(lines[:20])
    currency = None
    for pattern, code in _CURRENCY_HINTS:
        if re.search(pattern, header):
            currency = code
            break
    unit_match = _UNIT_RE.search(header)
    unit = unit_match.group(1) if unit_match else None
    unit_source = unit_match.string[max(0, unit_match.start() - 12) : unit_match.end()].strip() if unit_match else None
    return currency, unit, unit_source


def _field(value, page_number=None, source_text=None, note=None) -> dict:
    result = {"value": value, "page_number": page_number, "source_text": source_text}
    if note:
        result["note"] = note
    return result


def _field_from_item(item: StatementLineItem | None, note_if_missing: str | None = None) -> dict:
    if item is None:
        return _field(None, note=note_if_missing or "Not found in the document")
    return _field(item.values, item.page_number, item.source_text)


def _serialize_sections(sections: dict[str, StatementSection]) -> dict:
    return {
        name: [
            {
                "label": item.label,
                "values": item.values,
                "page_number": item.page_number,
                "source_text": item.source_text,
            }
            for item in section.items
        ]
        for name, section in sections.items()
    }


@dataclass
class ExtractionOutcome:
    extracted_data: dict
    periods: list[str]
    ocr_used: bool
    pages_processed: int
    sections: dict[str, StatementSection] = field(default_factory=dict)
    invoice_context: InvoiceContext | None = None


class ExtractionService:
    """Turns page text/geometry into structured, document-type-specific fields."""

    def extract(self, pages: list[PageText], document_type: DocumentType) -> ExtractionOutcome:
        ocr_used = any(page.ocr_used for page in pages)
        rows = [row for page in pages for row in page.rows]
        lines_with_pages = [(row.page_number, row.text) for row in rows if row.text.strip()]

        if document_type == DocumentType.INVOICE:
            return self._extract_invoice(lines_with_pages, ocr_used, len(pages), rows)

        periods, global_anchors = detect_period_columns(rows)
        page_anchors: dict[int, list[float]] = {}
        for page in pages:
            _, anchors = detect_period_columns(page.rows)
            page_anchors[page.page_number] = anchors if len(anchors) == len(global_anchors) else global_anchors

        logger.info("Detected periods=%s column anchors=%s", periods, page_anchors)

        if document_type == DocumentType.BALANCE_SHEET:
            return self._extract_balance_sheet(rows, periods, page_anchors, ocr_used, len(pages))
        if document_type == DocumentType.PROFIT_AND_LOSS:
            return self._extract_profit_and_loss(rows, periods, page_anchors, ocr_used, len(pages))
        return self._extract_cash_flow(rows, periods, page_anchors, ocr_used, len(pages))

    # ---- Invoice ----------------------------------------------------------

    def _extract_invoice(self, lines_with_pages, ocr_used: bool, pages_processed: int, rows=None) -> ExtractionOutcome:
        ctx = parse_invoice(lines_with_pages, rows)

        def ev(key: str) -> tuple[int | None, str | None]:
            return ctx.evidence.get(key, (None, None))

        extracted_data: dict = {
            "vendor_name": _field(ctx.vendor_name, *ev("vendor_name")),
            "vendor_address": _field(ctx.vendor_address, *ev("vendor_address")),
            "vendor_tax_id": _field(ctx.vendor_tax_id, *ev("vendor_tax_id")),
            "customer_name": _field(ctx.customer_name, *ev("customer_name")),
            "invoice_number": _field(ctx.invoice_number, *ev("invoice_number")),
            "invoice_date": _field(ctx.invoice_date, *ev("invoice_date")),
            "invoice_time": _field(ctx.invoice_time, *ev("invoice_time")),
            "currency": _field(ctx.currency, *ev("currency")),
            "subtotal": _field(ctx.subtotal, *ev("subtotal")),
            "tax_amount": _field(ctx.tax_amount, *ev("tax_amount")),
            "tax_rate_percent": _field(ctx.tax_rate_percent, *ev("tax_rate_percent")),
            "tax_inclusive": _field(ctx.tax_inclusive, *ev("tax_inclusive")),
            "discount": _field(ctx.discount, *ev("discount")),
            "additional_charges": _field(ctx.additional_charges, *ev("additional_charges")),
            "total_amount": _field(ctx.total_amount, *ev("total_amount")),
            "total_quantity": _field(ctx.total_quantity, *ev("total_quantity")),
            "cash_paid": _field(ctx.cash_paid, *ev("cash_paid")),
            "change": _field(ctx.change, *ev("change")),
            "line_items": [
                {
                    "description": item.description,
                    "quantity": item.quantity,
                    "unit_price": item.unit_price,
                    "amount": item.amount,
                    "page_number": item.page_number,
                    "source_text": item.source_text,
                }
                for item in ctx.line_items
            ],
        }
        return ExtractionOutcome(
            extracted_data=extracted_data,
            periods=["current"],
            ocr_used=ocr_used,
            pages_processed=pages_processed,
            invoice_context=ctx,
        )

    # ---- Balance sheet ----------------------------------------------------

    def _extract_balance_sheet(
        self, rows: list[LayoutRow], periods, page_anchors, ocr_used, pages_processed
    ) -> ExtractionOutcome:
        sections = scan_sections(
            rows, BALANCE_SHEET_SPEC, BALANCE_SHEET_STOP_MARKERS, periods, page_anchors
        )
        currency, unit, unit_source = _detect_currency_and_unit([row.text for row in rows])

        liabilities_total = find_in_sections(sections, "capital_and_liabilities", "total")
        assets_total = find_in_sections(sections, "assets", "total")

        extracted_data = {
            "statement_periods": periods,
            "currency": _field(currency),
            "unit_multiplier": _field(unit, source_text=unit_source, note="Figures are reported in these units"),
            "capital": _field_from_item(find_in_sections(sections, "capital_and_liabilities", "capital")),
            "reserves_and_surplus": _field_from_item(
                find_in_sections(sections, "capital_and_liabilities", "reserves and surplus", "reserves & surplus")
            ),
            "minority_interest": _field_from_item(
                find_in_sections(sections, "capital_and_liabilities", "minority interest")
            ),
            "deposits": _field_from_item(find_in_sections(sections, "capital_and_liabilities", "deposits")),
            "borrowings": _field_from_item(find_in_sections(sections, "capital_and_liabilities", "borrowings")),
            "other_liabilities_and_provisions": _field_from_item(
                find_in_sections(sections, "capital_and_liabilities", "other liabilities")
            ),
            "total_liabilities": _field_from_item(liabilities_total),
            "cash_and_balances_with_central_bank": _field_from_item(
                find_in_sections(sections, "assets", "cash and balances")
            ),
            "balances_with_banks": _field_from_item(find_in_sections(sections, "assets", "balances with banks")),
            "investments": _field_from_item(find_in_sections(sections, "assets", "investments")),
            "advances": _field_from_item(find_in_sections(sections, "assets", "advances")),
            "fixed_assets": _field_from_item(find_in_sections(sections, "assets", "fixed assets")),
            "other_assets": _field_from_item(find_in_sections(sections, "assets", "other assets")),
            "goodwill_on_consolidation": _field_from_item(
                find_in_sections(sections, "assets", "goodwill")
            ),
            "total_assets": _field_from_item(assets_total),
            "total_equity": _field(
                None,
                note="Not disclosed as a single line in this statement format; equity components are reported "
                "separately (capital, reserves_and_surplus, minority_interest)",
            ),
            "line_items": _serialize_sections(sections),
        }
        return ExtractionOutcome(
            extracted_data=extracted_data,
            periods=periods,
            ocr_used=ocr_used,
            pages_processed=pages_processed,
            sections=sections,
        )

    # ---- Profit & loss ----------------------------------------------------

    def _extract_profit_and_loss(
        self, rows: list[LayoutRow], periods, page_anchors, ocr_used, pages_processed
    ) -> ExtractionOutcome:
        sections = scan_sections(
            rows, PROFIT_AND_LOSS_SPEC, PROFIT_AND_LOSS_STOP_MARKERS, periods, page_anchors
        )
        currency, unit, unit_source = _detect_currency_and_unit([row.text for row in rows])

        total_income = find_in_sections(sections, "income", "total")
        total_expenditure = find_in_sections(sections, "expenditure", "total")
        # "Brought forward consolidated profit attributable to the group" carries
        # the same phrase as the profit row itself. Where a poor scan loses the
        # profit row, the lookup would otherwise land on the brought-forward row
        # and report one figure as both operands of the appropriation check.
        attributable = find_preferring_section(
            sections, "profit", "attributable to the group", exclude=("brought forward",)
        )
        total_appropriation = find_in_sections(sections, "appropriations", "total")

        extracted_data = {
            "statement_periods": periods,
            "currency": _field(currency),
            "unit_multiplier": _field(unit, source_text=unit_source),
            "interest_earned": _field_from_item(find_in_sections(sections, "income", "interest earned")),
            "other_income": _field_from_item(find_in_sections(sections, "income", "other income")),
            "total_income": _field_from_item(total_income),
            "interest_expended": _field_from_item(find_in_sections(sections, "expenditure", "interest expended")),
            "operating_expenses": _field_from_item(find_in_sections(sections, "expenditure", "operating expenses")),
            "provisions_and_contingencies": _field_from_item(
                find_in_sections(sections, "expenditure", "provisions and contingencies", "provisions & contingencies")
            ),
            "total_expenditure": _field_from_item(total_expenditure),
            "net_profit_for_the_year": _field_from_item(find_preferring_section(sections, "profit", "net profit for the year")),
            "minority_interest": _field_from_item(
                find_preferring_section(
                    sections,
                    "profit",
                    "minority interest",
                    # "Net Profit for the year *before* Minority Interest" is the
                    # profit row and "Transfer to / (from) Minority Interest" an
                    # appropriation; neither is the minority interest deduction.
                    exclude=("before minority interest", "transfer to", "increase in minority"),
                )
            ),
            "share_in_profits_of_associates": _field_from_item(find_preferring_section(sections, "profit", "share in profit")),
            "consolidated_profit_attributable_to_group": _field_from_item(attributable),
            "brought_forward_profit": _field_from_item(find_preferring_section(sections, "profit", "brought forward")),
            # Some years carry an extra row into the appropriation total
            # ("Impact on amalgamation", "Addition on amalgamation (net)").
            # Leaving it out makes the appropriation check fail on a document
            # that in fact adds up.
            "addition_on_amalgamation": _field_from_item(
                find_in_sections(sections, "profit", "on amalgamation")
            ),
            "total_available_for_appropriation": _field_from_item(total_appropriation),
            "balance_carried_to_balance_sheet": _field_from_item(
                find_preferring_section(sections, "appropriations", "carried over to", "balance carried")
            ),
            # Generic profit & loss aliases (case study section 2). Null where a
            # banking-format statement does not disclose an equivalent line.
            "revenue": _field_from_item(total_income, note_if_missing="Total income line not found"),
            "cost_of_sales": _field(None, note="Not applicable to a banking-format statement"),
            "gross_profit": _field(None, note="Not applicable to a banking-format statement"),
            "operating_profit": _field(None, note="Not applicable to a banking-format statement"),
            "tax": _field_from_item(
                find_preferring_section(sections, "appropriations", "tax (including cess)"),
                note_if_missing="Income tax is not disclosed as a separate line above the profit line",
            ),
            "net_profit": _field_from_item(attributable, note_if_missing="Not found in the document"),
            "line_items": _serialize_sections(sections),
        }
        return ExtractionOutcome(
            extracted_data=extracted_data,
            periods=periods,
            ocr_used=ocr_used,
            pages_processed=pages_processed,
            sections=sections,
        )

    # ---- Cash flow --------------------------------------------------------

    def _extract_cash_flow(
        self, rows: list[LayoutRow], periods, page_anchors, ocr_used, pages_processed
    ) -> ExtractionOutcome:
        sections = scan_sections(rows, CASH_FLOW_SPEC, CASH_FLOW_STOP_MARKERS, periods, page_anchors)
        currency, unit, unit_source = _detect_currency_and_unit([row.text for row in rows])

        # Within a cash-flow section the only "net cash" row is that section's
        # subtotal, however the wording is arranged ("Net cash flow from /
        # (used) in investing activities", "Net cash used in ...", etc).
        operating = find_in_sections(sections, "operating", "net cash")
        investing = find_in_sections(sections, "investing", "net cash")
        financing = find_in_sections(sections, "financing", "net cash")
        fx_adjustment = find_preferring_section(
            sections, "financing", "translation reserve", "exchange fluctuation", "foreign currency translation"
        )
        amalgamation = find_preferring_section(sections, "financing", "on amalgamation")
        net_increase = find_preferring_section(
            sections, "financing", "net increase", "net decrease", "net (decrease)"
        )
        opening_cash = find_preferring_section(
            sections, "financing", "beginning of", "as at april", "opening cash", "opening balance"
        )
        closing_cash = find_preferring_section(
            sections, "financing", "end of", "as at march", "closing cash", "closing balance"
        )

        extracted_data = {
            "statement_periods": periods,
            "currency": _field(currency),
            "unit_multiplier": _field(unit, source_text=unit_source),
            "profit_before_tax": _field_from_item(find_preferring_section(sections, "operating", "profit before income tax")),
            "operating_cash_flow": _field_from_item(operating),
            "investing_cash_flow": _field_from_item(investing),
            "financing_cash_flow": _field_from_item(financing),
            "fx_translation_adjustment": _field_from_item(
                fx_adjustment, note_if_missing="No exchange/translation adjustment line in this statement"
            ),
            "cash_on_amalgamation_adjustment": _field_from_item(
                amalgamation, note_if_missing="No amalgamation adjustment line in this statement"
            ),
            "net_change_in_cash": _field_from_item(net_increase),
            "opening_cash": _field_from_item(opening_cash),
            "closing_cash": _field_from_item(closing_cash),
            "line_items": _serialize_sections(sections),
        }
        return ExtractionOutcome(
            extracted_data=extracted_data,
            periods=periods,
            ocr_used=ocr_used,
            pages_processed=pages_processed,
            sections=sections,
        )
