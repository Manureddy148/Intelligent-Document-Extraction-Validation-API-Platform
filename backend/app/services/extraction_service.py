import re
from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.schemas.extraction import DocumentType
from app.services.ocr_service import PageText
from app.utils.invoice_parsing import InvoiceContext, parse_invoice
from app.utils.statement_sections import (
    BALANCE_SHEET_SECTION_MARKERS,
    BALANCE_SHEET_STOP_MARKERS,
    CASH_FLOW_SECTION_MARKERS,
    CASH_FLOW_STOP_MARKERS,
    PROFIT_AND_LOSS_SECTION_MARKERS,
    PROFIT_AND_LOSS_STOP_MARKERS,
    find_in_sections,
    scan_sections,
)
from app.utils.text_parsing import StatementLineItem, StatementSection, extract_period_labels

logger = get_logger(__name__)

_UNIT_RE = re.compile(r"in\s*['’]?\s*(000|00,000|lakh|lakhs|crore|crores|million)", re.IGNORECASE)
_CURRENCY_HINTS = [
    (r"\bINR\b|₹|Rs\.?\b", "INR"),
    (r"\bUSD\b|\$", "USD"),
    (r"\bEUR\b|€", "EUR"),
    (r"\bRM\b", "MYR"),
]


def _detect_currency_and_unit(lines: list[str]) -> tuple[str | None, str | None]:
    sample = "\n".join(lines[:15])
    currency = None
    for pattern, code in _CURRENCY_HINTS:
        if re.search(pattern, sample):
            currency = code
            break
    unit_match = _UNIT_RE.search(sample)
    unit = unit_match.group(1) if unit_match else None
    return currency, unit


def _field(value, page_number=None, source_text=None, note=None) -> dict:
    result = {"value": value, "page_number": page_number, "source_text": source_text}
    if note:
        result["note"] = note
    return result


def _field_from_item(item: StatementLineItem | None, note_if_missing: str | None = None) -> dict:
    if item is None:
        return _field(None, note=note_if_missing)
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
    """Parses OCR/native text into structured, document-type-specific fields."""

    def extract(self, pages: list[PageText], document_type: DocumentType) -> ExtractionOutcome:
        ocr_used = any(p.ocr_used for p in pages)
        lines_with_pages = [
            (p.page_number, line.strip())
            for p in pages
            for line in p.text.splitlines()
            if line.strip()
        ]
        all_lines = [line for _, line in lines_with_pages]

        if document_type == DocumentType.INVOICE:
            return self._extract_invoice(lines_with_pages, ocr_used, len(pages))
        if document_type == DocumentType.BALANCE_SHEET:
            return self._extract_balance_sheet(lines_with_pages, all_lines, ocr_used, len(pages))
        if document_type == DocumentType.PROFIT_AND_LOSS:
            return self._extract_profit_and_loss(lines_with_pages, all_lines, ocr_used, len(pages))
        return self._extract_cash_flow(lines_with_pages, all_lines, ocr_used, len(pages))

    def _extract_invoice(self, lines_with_pages, ocr_used: bool, pages_processed: int) -> ExtractionOutcome:
        ctx = parse_invoice(lines_with_pages)

        def ev(key: str) -> tuple[int | None, str | None]:
            return ctx.evidence.get(key, (None, None))

        extracted_data: dict = {
            "vendor_name": _field(ctx.vendor_name),
            "invoice_number": _field(ctx.invoice_number, *ev("invoice_number")),
            "invoice_date": _field(ctx.invoice_date),
            "currency": _field(ctx.currency),
            "subtotal": _field(ctx.subtotal, *ev("subtotal")),
            "tax_amount": _field(ctx.tax_amount, *ev("tax_amount")),
            "discount": _field(ctx.discount),
            "total_amount": _field(ctx.total_amount, *ev("total_amount")),
            "cash_paid": _field(ctx.cash_paid, *ev("cash_paid")),
            "change": _field(ctx.change, *ev("change")),
            "line_items": [
                {
                    "description": li.description,
                    "quantity": li.quantity,
                    "unit_price": li.unit_price,
                    "amount": li.amount,
                }
                for li in ctx.line_items
            ],
        }
        return ExtractionOutcome(
            extracted_data=extracted_data,
            periods=["current"],
            ocr_used=ocr_used,
            pages_processed=pages_processed,
            invoice_context=ctx,
        )

    def _extract_balance_sheet(self, lines_with_pages, all_lines, ocr_used, pages_processed) -> ExtractionOutcome:
        periods = extract_period_labels(all_lines)
        sections = scan_sections(lines_with_pages, BALANCE_SHEET_SECTION_MARKERS, BALANCE_SHEET_STOP_MARKERS, periods)
        currency, unit = _detect_currency_and_unit(all_lines)

        cl = find_in_sections(sections, "capital_and_liabilities", "total")
        assets_total = find_in_sections(sections, "assets", "total")

        extracted_data = {
            "statement_periods": periods,
            "currency": _field(currency),
            "unit": _field(unit, note="Multiplier applied to reported figures, e.g. amounts in thousands"),
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
            "total_liabilities": _field_from_item(cl, note_if_missing="Total capital & liabilities line not found"),
            "cash_and_balances_with_rbi": _field_from_item(
                find_in_sections(sections, "assets", "cash and balances")
            ),
            "balances_with_banks": _field_from_item(find_in_sections(sections, "assets", "balances with banks")),
            "investments": _field_from_item(find_in_sections(sections, "assets", "investments")),
            "advances": _field_from_item(find_in_sections(sections, "assets", "advances")),
            "fixed_assets": _field_from_item(find_in_sections(sections, "assets", "fixed assets")),
            "other_assets": _field_from_item(find_in_sections(sections, "assets", "other assets")),
            "total_assets": _field_from_item(assets_total, note_if_missing="Total assets line not found"),
            "total_equity": _field(
                None,
                note="Not separately disclosed as a single line in this statement format; see capital/reserves/minority_interest",
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

    def _extract_profit_and_loss(self, lines_with_pages, all_lines, ocr_used, pages_processed) -> ExtractionOutcome:
        periods = extract_period_labels(all_lines)
        sections = scan_sections(
            lines_with_pages, PROFIT_AND_LOSS_SECTION_MARKERS, PROFIT_AND_LOSS_STOP_MARKERS, periods
        )
        currency, unit = _detect_currency_and_unit(all_lines)

        total_income = find_in_sections(sections, "income", "total")
        total_expenditure = find_in_sections(sections, "expenditure", "total")
        net_profit_attributable = find_in_sections(sections, "profit", "attributable to the group")
        total_appropriation = find_in_sections(sections, "appropriations", "total")

        extracted_data = {
            "statement_periods": periods,
            "currency": _field(currency),
            "unit": _field(unit),
            "interest_earned": _field_from_item(find_in_sections(sections, "income", "interest earned")),
            "other_income": _field_from_item(find_in_sections(sections, "income", "other income")),
            "total_income": _field_from_item(total_income),
            "interest_expended": _field_from_item(find_in_sections(sections, "expenditure", "interest expended")),
            "operating_expenses": _field_from_item(find_in_sections(sections, "expenditure", "operating expenses")),
            "provisions_and_contingencies": _field_from_item(
                find_in_sections(sections, "expenditure", "provisions and contingencies", "provisions & contingencies")
            ),
            "total_expenditure": _field_from_item(total_expenditure),
            "net_profit_for_the_year": _field_from_item(
                find_in_sections(sections, "profit", "net profit for the year")
            ),
            "minority_interest": _field_from_item(find_in_sections(sections, "profit", "minority interest")),
            "share_in_profits_of_associates": _field_from_item(
                find_in_sections(sections, "profit", "share in profit")
            ),
            "consolidated_profit_attributable_to_group": _field_from_item(net_profit_attributable),
            "brought_forward_profit": _field_from_item(find_in_sections(sections, "profit", "brought forward")),
            "total_available_for_appropriation": _field_from_item(total_appropriation),
            # Generic minimum-field aliases (spec section 2); null where the bank
            # statement format does not separately disclose an equivalent line.
            "revenue": _field_from_item(total_income, note_if_missing="Not present"),
            "cost_of_sales": _field(None, note="Not applicable to this statement format"),
            "gross_profit": _field(None, note="Not applicable to this statement format"),
            "operating_profit": _field(None, note="Not applicable to this statement format"),
            "tax": _field(None, note="Not separately disclosed above the profit line in this statement"),
            "net_profit": _field_from_item(net_profit_attributable, note_if_missing="Not present"),
            "line_items": _serialize_sections(sections),
        }
        return ExtractionOutcome(
            extracted_data=extracted_data,
            periods=periods,
            ocr_used=ocr_used,
            pages_processed=pages_processed,
            sections=sections,
        )

    def _extract_cash_flow(self, lines_with_pages, all_lines, ocr_used, pages_processed) -> ExtractionOutcome:
        periods = extract_period_labels(all_lines)
        sections = scan_sections(lines_with_pages, CASH_FLOW_SECTION_MARKERS, CASH_FLOW_STOP_MARKERS, periods)
        currency, unit = _detect_currency_and_unit(all_lines)

        net_operating = find_in_sections(sections, "operating", "net cash flow", "net cash used in", "net cash generated")
        net_investing = find_in_sections(sections, "investing", "net cash used in investing", "net cash generated from investing", "net cash flow from investing")
        net_financing = find_in_sections(sections, "financing", "net cash generated from financing", "net cash used in financing")
        fx_adjustment = find_in_sections(sections, "financing", "exchange fluctuation", "translation reserve")
        amalgamation = find_in_sections(sections, "financing", "amalgamation")
        net_increase = find_in_sections(sections, "financing", "net increase", "net decrease in cash")
        opening_cash = find_in_sections(sections, "financing", "as at april", "opening")
        closing_cash = find_in_sections(sections, "financing", "as at march", "closing")

        extracted_data = {
            "statement_periods": periods,
            "currency": _field(currency),
            "unit": _field(unit),
            "operating_cash_flow": _field_from_item(net_operating),
            "investing_cash_flow": _field_from_item(net_investing),
            "financing_cash_flow": _field_from_item(net_financing),
            "fx_translation_adjustment": _field_from_item(fx_adjustment),
            "cash_on_amalgamation_adjustment": _field_from_item(amalgamation),
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
