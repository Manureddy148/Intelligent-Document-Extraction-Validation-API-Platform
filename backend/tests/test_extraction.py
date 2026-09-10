from app.core.config import get_settings
from app.schemas.extraction import ValidationStatus
from app.services.extraction_service import ExtractionOutcome
from app.services.financial_validation_service import FinancialValidationService
from app.utils.invoice_parsing import parse_invoice
from app.utils.text_parsing import (
    StatementLineItem,
    StatementSection,
    extract_period_labels,
    parse_number,
    split_label_and_numbers,
)


def test_parse_number_handles_commas_and_negatives():
    assert parse_number("1,234,567.89") == 1234567.89
    assert parse_number("(1,234)") == -1234.0
    assert parse_number("-") is None
    assert parse_number("42") == 42.0


def test_split_label_and_numbers_drops_schedule_column():
    parsed = split_label_and_numbers("Capital 1 5,125,091 5,056,373")
    assert parsed is not None
    assert parsed.label == "Capital"
    assert parsed.numbers == [1.0, 5125091.0, 5056373.0]


def test_extract_period_labels_finds_dates():
    lines = ["Consolidated Balance Sheet", "As at As at", "Schedule 31-Mar-17 31-Mar-16", "Capital 1 5,125,091 5,056,373"]
    assert extract_period_labels(lines) == ["31-Mar-17", "31-Mar-16"]


def test_extract_period_labels_falls_back_when_no_dates():
    assert extract_period_labels(["no dates here"]) == ["period_1", "period_2"]


def test_parse_invoice_extracts_cash_change_and_total():
    lines = [
        (1, "FUYI MINI MARKET"),
        (1, "013 SUMMER CUP 48X230ML"),
        (1, "Total Includes GST 6% 9.00"),
        (1, "Cash 50.00"),
        (1, "Change 41.00"),
    ]
    ctx = parse_invoice(lines)
    assert ctx.total_amount == 9.00
    assert ctx.cash_paid == 50.00
    assert ctx.change == 41.00


def test_financial_validation_cash_change_check_passes():
    from app.utils.invoice_parsing import InvoiceContext

    ctx = InvoiceContext(total_amount=9.00, cash_paid=50.00, change=41.00)
    outcome = ExtractionOutcome(extracted_data={}, periods=["current"], ocr_used=True, pages_processed=1, invoice_context=ctx)

    from app.schemas.extraction import DocumentType

    service = FinancialValidationService(get_settings())
    result = service.validate(DocumentType.INVOICE, outcome)
    cash_check = next(c for c in result.checks if c.name == "cash_change_check")
    assert cash_check.status == ValidationStatus.PASS
    assert cash_check.variance == 0.0


def test_financial_validation_balance_sheet_flags_mismatch():
    from app.schemas.extraction import DocumentType

    periods = ["31-Mar-17", "31-Mar-16"]
    cl_section = StatementSection(name="capital_and_liabilities")
    cl_section.items.append(
        StatementLineItem(
            label="Capital",
            key="capital_and_liabilities__capital",
            values={"31-Mar-17": 100.0, "31-Mar-16": 90.0},
            page_number=1,
            source_text="Capital 100.00 90.00",
        )
    )
    cl_section.items.append(
        StatementLineItem(
            label="Total",
            key="capital_and_liabilities__total",
            values={"31-Mar-17": 100.0, "31-Mar-16": 90.0},
            page_number=1,
            source_text="Total 100.00 90.00",
        )
    )
    assets_section = StatementSection(name="assets")
    assets_section.items.append(
        StatementLineItem(
            label="Cash",
            key="assets__cash",
            values={"31-Mar-17": 50.0, "31-Mar-16": 90.0},
            page_number=1,
            source_text="Cash 50.00 90.00",
        )
    )
    assets_section.items.append(
        StatementLineItem(
            label="Total",
            key="assets__total",
            values={"31-Mar-17": 50.0, "31-Mar-16": 90.0},
            page_number=1,
            source_text="Total 50.00 90.00",
        )
    )

    outcome = ExtractionOutcome(
        extracted_data={},
        periods=periods,
        ocr_used=False,
        pages_processed=1,
        sections={"capital_and_liabilities": cl_section, "assets": assets_section},
    )

    service = FinancialValidationService(get_settings())
    result = service.validate(DocumentType.BALANCE_SHEET, outcome)

    mismatch_check = next(c for c in result.checks if c.name == "balance_sheet_equality[31-Mar-17]")
    match_check = next(c for c in result.checks if c.name == "balance_sheet_equality[31-Mar-16]")
    assert mismatch_check.status == ValidationStatus.FAIL
    assert match_check.status == ValidationStatus.PASS
    assert result.overall_status == ValidationStatus.FAIL
