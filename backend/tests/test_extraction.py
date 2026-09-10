from app.core.config import Settings, get_settings
from app.schemas.extraction import DocumentType, ValidationStatus
from app.services.extraction_service import ExtractionOutcome
from app.services.financial_validation_service import FinancialValidationService
from app.services.llm_extraction_service import LlmExtractionService
from app.utils.invoice_parsing import InvoiceContext, parse_invoice
from app.utils.layout import Word, detect_period_columns, group_words_into_rows, split_row
from app.utils.text_parsing import StatementLineItem, StatementSection, parse_number


def _word(text, x1, top=0.0, width=60.0, height=10.0):
    return Word(text=text, x0=x1 - width, x1=x1, top=top, bottom=top + height)


def test_parse_number_handles_accounting_formats():
    assert parse_number("1,234,567.89") == 1234567.89
    assert parse_number("(1,234)") == -1234.0
    assert parse_number("-") is None
    assert parse_number("42") == 42.0


def test_rows_are_rebuilt_from_vertical_position():
    """Words emitted out of order still group into the row they were printed on."""
    words = [
        _word("Capital", 300, top=100),
        _word("Deposits", 300, top=130),
        _word("5,125,091", 1300, top=101),
        _word("6,431,342", 1300, top=131),
    ]
    rows = group_words_into_rows(words, page_number=1)
    assert [row.text for row in rows] == ["Capital 5,125,091", "Deposits 6,431,342"]


def test_period_columns_detected_from_dash_dates():
    words = [_word("31-Mar-17", 1300, top=10), _word("31-Mar-16", 1540, top=10)]
    words += [_word("Capital", 300, top=40), _word("5,125,091", 1300, top=40), _word("5,056,373", 1540, top=40)]
    rows = group_words_into_rows(words, 1)
    periods, anchors = detect_period_columns(rows)
    assert periods == ["31-Mar-17", "31-Mar-16"]
    assert len(anchors) == 2


def test_period_columns_detected_from_month_name_dates():
    header = [
        _word("March", 1200, top=10, width=70),
        _word("31,", 1240, top=10, width=30),
        _word("2024", 1300, top=10, width=50),
        _word("March", 1400, top=10, width=70),
        _word("31,", 1440, top=10, width=30),
        _word("2023", 1500, top=10, width=50),
    ]
    body = [_word("Deposits", 300, top=40), _word("1,234.00", 1300, top=40), _word("1,000.00", 1500, top=40)]
    rows = group_words_into_rows(header + body, 1)
    periods, _ = detect_period_columns(rows)
    assert periods == ["March 31, 2024", "March 31, 2023"]


def test_lone_value_is_assigned_to_its_own_period_column():
    """A row populated for only the later period must not be read as the earlier one."""
    header = [_word("31-Mar-17", 1300, top=10), _word("31-Mar-16", 1540, top=10)]
    full_row = [_word("Interest earned", 300, top=40), _word("732,713", 1300, top=40), _word("631,615", 1540, top=40)]
    lone_row = [_word("Total", 300, top=70), _word("743,732", 1540, top=70)]
    rows = group_words_into_rows(header + full_row + lone_row, 1)
    periods, anchors = detect_period_columns(rows)

    total_row = next(row for row in rows if row.text.startswith("Total"))
    label, values = split_row(total_row, anchors)
    assert label == "Total"
    assert values == {1: 743732.0}  # second period only, not the first


def test_schedule_reference_column_is_not_read_as_a_value():
    header = [_word("31-Mar-17", 1300, top=10), _word("31-Mar-16", 1540, top=10)]
    row = [
        _word("Capital", 300, top=40),
        _word("1", 1029, top=40, width=12),
        _word("5,125,091", 1300, top=40),
        _word("5,056,373", 1540, top=40),
    ]
    rows = group_words_into_rows(header + row, 1)
    _, anchors = detect_period_columns(rows)
    label, values = split_row(rows[-1], anchors)
    assert label == "Capital"
    assert values == {0: 5125091.0, 1: 5056373.0}


def test_parse_invoice_extracts_totals_and_payment():
    lines = [
        (1, "FUYI MINI MARKET"),
        (1, "GST No. : 001603310720"),
        (1, "25/01/2018 1:22:56PM"),
        (1, "SUMMER CUP 1 8.49 8.49"),
        (1, "GST6%+ 0.51"),
        (1, "Total Includes GST 6% 9.00"),
        (1, "Cash 50.00"),
        (1, "Change 41.00"),
    ]
    ctx = parse_invoice(lines)
    assert ctx.total_amount == 9.00
    assert ctx.cash_paid == 50.00
    assert ctx.change == 41.00
    assert ctx.tax_amount == 0.51
    assert ctx.tax_rate_percent == 6.0
    assert ctx.invoice_date == "25/01/2018"
    assert ctx.tax_inclusive is True


def _statement_outcome(extracted: dict, periods: list[str], sections=None) -> ExtractionOutcome:
    return ExtractionOutcome(
        extracted_data=extracted,
        periods=periods,
        ocr_used=True,
        pages_processed=1,
        sections=sections or {},
    )


def _field(values):
    return {"value": values, "page_number": 1, "source_text": "row"}


def test_balance_sheet_equality_flags_only_the_mismatched_period():
    outcome = _statement_outcome(
        {
            "total_liabilities": _field({"2024": 100.0, "2023": 90.0}),
            "total_assets": _field({"2024": 50.0, "2023": 90.0}),
        },
        ["2024", "2023"],
    )
    result = FinancialValidationService(get_settings()).validate(DocumentType.BALANCE_SHEET, outcome)
    by_name = {check.name: check for check in result.checks}
    assert by_name["balance_sheet_equality[2024]"].status == ValidationStatus.FAIL
    assert by_name["balance_sheet_equality[2023]"].status == ValidationStatus.PASS
    assert result.overall_status == ValidationStatus.FAIL


def test_partially_read_section_is_not_applicable_rather_than_failed():
    """An unread component row must not be silently treated as zero."""
    section = StatementSection(name="assets")
    section.items = [
        StatementLineItem("Cash", "assets__cash", {"2024": 40.0}, 1, "Cash 40"),
        StatementLineItem("Advances", "assets__advances", {"2024": None}, 1, "Advances"),
        StatementLineItem("Total", "assets__total", {"2024": 100.0}, 1, "Total 100"),
    ]
    outcome = _statement_outcome(
        {"total_liabilities": _field({"2024": 100.0}), "total_assets": _field({"2024": 100.0})},
        ["2024"],
        {"assets": section},
    )
    result = FinancialValidationService(get_settings()).validate(DocumentType.BALANCE_SHEET, outcome)
    reconciliation = next(c for c in result.checks if c.name == "assets_reconciliation[2024]")
    assert reconciliation.status == ValidationStatus.NOT_APPLICABLE


def test_cash_flow_checks_reconcile():
    outcome = _statement_outcome(
        {
            "operating_cash_flow": _field({"2024": 19069.34}),
            "investing_cash_flow": _field({"2024": 5313.77}),
            "financing_cash_flow": _field({"2024": -3983.06}),
            "fx_translation_adjustment": _field({"2024": 104.94}),
            "net_change_in_cash": _field({"2024": 20504.99}),
            "opening_cash": _field({"2024": 197147.81}),
            "closing_cash": _field({"2024": 228834.51}),
            "cash_on_amalgamation_adjustment": _field({"2024": 11181.71}),
        },
        ["2024"],
    )
    result = FinancialValidationService(get_settings()).validate(DocumentType.CASH_FLOW_STATEMENT, outcome)
    assert all(check.status == ValidationStatus.PASS for check in result.checks)


def test_profit_and_loss_missing_operand_is_not_applicable():
    outcome = _statement_outcome(
        {"interest_earned": _field({"2024": 100.0}), "other_income": _field(None), "total_income": _field({"2024": 150.0})},
        ["2024"],
    )
    result = FinancialValidationService(get_settings()).validate(DocumentType.PROFIT_AND_LOSS, outcome)
    income_check = next(c for c in result.checks if c.name == "total_income_check[2024]")
    assert income_check.status == ValidationStatus.NOT_APPLICABLE
    assert "other_income" in income_check.message


def test_invoice_cash_change_check_passes():
    ctx = InvoiceContext(total_amount=9.00, cash_paid=50.00, change=41.00)
    outcome = ExtractionOutcome({}, ["current"], True, 1, invoice_context=ctx)
    result = FinancialValidationService(get_settings()).validate(DocumentType.INVOICE, outcome)
    cash_check = next(c for c in result.checks if c.name == "cash_change_check")
    assert cash_check.status == ValidationStatus.PASS
    assert cash_check.variance == 0.0


def test_llm_service_is_disabled_without_an_api_key():
    service = LlmExtractionService(Settings(llm_api_key=None))
    assert service.enabled is False
    assert service.recover_missing_fields("some text", "invoice", ["total_amount"]) == {}


def test_llm_recovery_keeps_only_non_null_values(monkeypatch):
    service = LlmExtractionService(Settings(llm_api_key="test-key"))
    monkeypatch.setattr(
        service,
        "_call_model",
        lambda *args, **kwargs: {
            "total_amount": {"value": 9.0, "page_number": 1, "source_text": "Total 9.00"},
            "discount": {"value": None, "page_number": None, "source_text": None},
        },
    )
    recovered = service.recover_missing_fields("text", "invoice", ["total_amount", "discount"])
    assert set(recovered) == {"total_amount"}
    assert recovered["total_amount"]["extraction_method"] == "llm_assisted"


def test_llm_failure_falls_back_silently(monkeypatch):
    service = LlmExtractionService(Settings(llm_api_key="test-key"))

    def _boom(*args, **kwargs):
        raise RuntimeError("api unavailable")

    monkeypatch.setattr(service, "_call_model", _boom)
    assert service.recover_missing_fields("text", "invoice", ["total_amount"]) == {}


def test_invoice_heading_is_not_mistaken_for_an_identifier():
    """'TAX INVOICE' is a heading, not a tax id or an invoice number."""
    ctx = parse_invoice([(1, "TAX INVOICE"), (1, "COUNTER 1 CASHIER: HOCK")])
    assert ctx.invoice_number is None
    assert ctx.vendor_tax_id is None


def test_invoice_identifiers_are_read_when_present():
    ctx = parse_invoice([(1, "TRN: 1CRO576494"), (1, "GST No. : 001603310720")])
    assert ctx.invoice_number == "1CRO576494"
    assert ctx.vendor_tax_id == "001603310720"
