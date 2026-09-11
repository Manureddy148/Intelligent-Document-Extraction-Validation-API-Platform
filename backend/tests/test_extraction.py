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


def test_number_split_across_tokens_is_rejoined():
    """OCR breaks long figures in half; the fragments must not read as a value."""
    header = [_word("31-Mar-19", 1296, top=10), _word("31-Mar-18", 1532, top=10)]
    # "11,031,861,695" came back as "11,031" + ",861,695" sitting flush together.
    row = [
        _word("Total", 1019, top=40, width=50),
        _word("12,928,057,065", 1300, top=40, width=154),
        _word("11,031", 1442, top=40, width=61),
        _word(",861,695", 1532, top=40, width=83),
    ]
    rows = group_words_into_rows(header + row, 1)
    _, anchors = detect_period_columns(rows)
    label, values = split_row(rows[-1], anchors)
    assert label == "Total"
    assert values == {0: 12928057065.0, 1: 11031861695.0}


def test_separate_column_values_are_not_merged():
    """Two real values in different columns sit far apart and must stay separate."""
    header = [_word("31-Mar-19", 1296, top=10), _word("31-Mar-18", 1532, top=10)]
    row = [_word("Capital", 300, top=40), _word("5,446,613", 1296, top=40), _word("5,190,181", 1532, top=40)]
    rows = group_words_into_rows(header + row, 1)
    _, anchors = detect_period_columns(rows)
    label, values = split_row(rows[-1], anchors)
    assert values == {0: 5446613.0, 1: 5190181.0}


def test_vendor_name_skips_ocr_noise_and_headings():
    """A 4-letter scrap or a "TAX INVOICE" heading is not a vendor name."""
    ctx = parse_invoice([(1, "cudd"), (1, "TAX INVOICE"), (1, "FUYI MINI MARKET")])
    assert ctx.vendor_name == "FUYI MINI MARKET"


def test_vendor_name_is_null_when_unreadable():
    ctx = parse_invoice([(1, "roy"), (1, "O.2 61"), (1, "Total 9.00")])
    assert ctx.vendor_name is None


def test_vendor_name_rejects_symbol_heavy_ocr_noise():
    """Mostly-punctuation lines are OCR noise, not a vendor name."""
    ctx = parse_invoice([(1, "<B R WNa.: pity 01"), (1, "FUYI MINI MARKET")])
    assert ctx.vendor_name == "FUYI MINI MARKET"


def test_vendor_name_rejects_line_item_column_headers():
    ctx = parse_invoice([(1, "Qty UOM U.Price Amt Tax Code"), (1, "BEMED (SP) SDN. BHD.")])
    assert ctx.vendor_name == "BEMED (SP) SDN. BHD."


def test_vendor_name_is_only_taken_from_the_top_of_the_document():
    """A name-like line far down the page is not the vendor."""
    lines = [(1, "%%%"), (1, "@@@"), (1, "###"), (1, "***"), (1, "!!!"), (1, "^^^"), (1, "Customers Payment Details")]
    assert parse_invoice(lines).vendor_name is None


def test_invoice_total_check_is_not_applicable_when_tax_was_not_read():
    """An unread tax line must not be treated as zero and reported as a failure."""
    ctx = InvoiceContext(subtotal=135.00, tax_amount=None, total_amount=157.48)
    outcome = ExtractionOutcome({}, ["current"], True, 1, invoice_context=ctx)
    result = FinancialValidationService(get_settings()).validate(DocumentType.INVOICE, outcome)
    check = next(c for c in result.checks if c.name == "invoice_total_check")
    assert check.status == ValidationStatus.NOT_APPLICABLE


def test_invoice_total_check_passes_when_all_parts_are_present():
    ctx = InvoiceContext(subtotal=135.00, tax_amount=22.48, total_amount=157.48)
    outcome = ExtractionOutcome({}, ["current"], True, 1, invoice_context=ctx)
    result = FinancialValidationService(get_settings()).validate(DocumentType.INVOICE, outcome)
    check = next(c for c in result.checks if c.name == "invoice_total_check")
    assert check.status == ValidationStatus.PASS


def test_receipt_line_items_span_two_rows():
    """Receipts print the description on one row and its figures on the next."""
    ctx = parse_invoice([
        (1, "Smoked Duck Spaghetti"),
        (1, "1x 12.50 12.50 SR"),
        (1, "Hot Green Tea"),
        (1, "2x 3.00 6.00 SR"),
    ])
    assert [(i.description, i.quantity, i.unit_price, i.amount) for i in ctx.line_items] == [
        ("Smoked Duck Spaghetti", 1.0, 12.50, 12.50),
        ("Hot Green Tea", 2.0, 3.00, 6.00),
    ]


def test_tax_exclusive_and_inclusive_totals_are_told_apart():
    ctx = parse_invoice([
        (1, "Total (Excluding GST): 28.58"),
        (1, "GST payable (6%): 1,72"),
        (1, "Total (Inclusive of GST): 30.30"),
    ])
    assert ctx.subtotal == 28.58
    assert ctx.tax_amount == 1.72   # OCR wrote the decimal point as a comma
    assert ctx.total_amount == 30.30


def test_comma_thousands_separator_is_not_read_as_a_decimal():
    ctx = parse_invoice([(1, "Total 1,720.00")])
    assert ctx.total_amount == 1720.00


def test_invoice_table_row_with_item_number_and_currency():
    """A real invoice table row: item no, description, qty, rate, cost, amount."""
    ctx = parse_invoice([
        (1, "ITEM DESCRIPTION QTY PRICE AMOUNT"),
        (1, "5991 3M SJ3550 Dual Lock Fastener 9 $367 $367 $3303"),
        (1, "Total $3303"),
    ])
    item = ctx.line_items[0]
    assert (item.description, item.quantity, item.unit_price, item.amount) == (
        "3M SJ3550 Dual Lock Fastener", 9.0, 367.0, 3303.0
    )


def test_letterhead_outside_the_item_table_is_not_a_line_item():
    """Without a table region, "Tel. 416 431 0440" became a 416-quantity item."""
    ctx = parse_invoice([
        (1, "A.E. Blake Sales Ltd. Tel. 416 431 0440"),
        (1, "Invoice NO: 138236"),
    ])
    assert ctx.line_items == []


def test_unit_column_between_price_and_amount_is_skipped():
    """"24 $23.50 KIT $564.00" - the U/M column sits between price and amount."""
    ctx = parse_invoice([
        (1, "# ITEM DESCRIPTION QTY PRICE U/M AMOUNT"),
        (1, "30 100237 BC 27767 24 $23.50 KIT $564.00"),
        (1, "S. Total: $ 564.00"),
    ])
    item = ctx.line_items[0]
    assert (item.quantity, item.unit_price, item.amount) == (24.0, 23.50, 564.00)


def test_unreadable_table_row_marks_the_item_list_incomplete():
    """A partial item list must not be reported as a reconciliation failure."""
    ctx = parse_invoice([
        (1, "ITEM DESCRIPTION QTY PRICE AMOUNT"),
        (1, "Widget 2 5.00 10.00"),
        (1, "99.99"),
        (1, "Total 110.00"),
    ])
    assert ctx.line_items_incomplete is True


def test_currency_amounts_without_decimals_are_read():
    assert parse_invoice([(1, "Total: $5257")]).total_amount == 5257.0
    assert parse_invoice([(1, "Total RM 50")]).total_amount == 50.0


def test_month_name_invoice_dates_are_read():
    assert parse_invoice([(1, "Invoice date Oct. 13, 2023")]).invoice_date == "Oct. 13, 2023"
    assert parse_invoice([(1, "Dated 13 March 2024")]).invoice_date == "13 March 2024"


def test_column_header_and_total_rows_are_not_line_items():
    ctx = parse_invoice([
        (1, "No. Description Quantity Rate Cost Amount"),
        (1, "Total 3 items 45.00 90.00"),
    ])
    assert ctx.line_items == []


def test_line_item_sum_is_not_compared_against_a_tax_inclusive_total():
    """Items sum to the net amount; a tax-inclusive total legitimately differs."""
    from app.utils.invoice_parsing import InvoiceLineItem

    ctx = InvoiceContext(
        total_amount=30.30, tax_amount=1.72, tax_inclusive=True,
        line_items=[InvoiceLineItem("Tea", 1, 28.58, 28.58)],
    )
    outcome = ExtractionOutcome({}, ["current"], True, 1, invoice_context=ctx)
    result = FinancialValidationService(get_settings()).validate(DocumentType.INVOICE, outcome)
    check = next(c for c in result.checks if c.name == "line_items_sum_reconciliation")
    assert check.status == ValidationStatus.NOT_APPLICABLE


def test_line_item_sum_reconciles_against_a_printed_subtotal():
    from app.utils.invoice_parsing import InvoiceLineItem

    ctx = InvoiceContext(
        subtotal=28.58, total_amount=30.30, tax_amount=1.72,
        line_items=[InvoiceLineItem("Tea", 1, 28.58, 28.58)],
    )
    outcome = ExtractionOutcome({}, ["current"], True, 1, invoice_context=ctx)
    result = FinancialValidationService(get_settings()).validate(DocumentType.INVOICE, outcome)
    check = next(c for c in result.checks if c.name == "line_items_sum_reconciliation")
    assert check.status == ValidationStatus.PASS


def test_gst_included_in_total_line_is_tax_not_the_total():
    """"GST @6% included in total RM 0.35" replaced a RM 6.20 bill with RM 0.35."""
    ctx = parse_invoice([
        (1, "Total Incl. GST&6% RM 6.20"),
        (1, "CASH RM 100.00"),
        (1, "CHANGE RM 93.80"),
        (1, "GST @6% included in total RM 0.35"),
    ])
    assert ctx.total_amount == 6.20
    assert ctx.tax_amount == 0.35
    assert ctx.tax_inclusive is True


def test_two_digit_comma_group_is_a_decimal_point():
    """OCR reads "554.55" as "554,55"; as a thousands separator it becomes 55455."""
    assert parse_number("554,55") == 554.55
    assert parse_number("2,530,432.44") == 2530432.44
    assert parse_number("1,234,567") == 1234567.0


def test_minority_interest_is_not_read_off_the_profit_row():
    section = StatementSection(name="profit")
    section.items = [
        StatementLineItem(
            "Consolidated Net Profit for the year before Minority Interest",
            "profit__before", {"2026": 79219.46}, 1, "row",
        ),
        StatementLineItem("Less : Minority Interest", "profit__mi", {"2026": 3193.49}, 1, "row"),
    ]
    assert section.find("minority interest").values["2026"] == 79219.46
    assert section.find("minority interest", exclude=("before minority interest",)).values["2026"] == 3193.49


def test_canadian_invoice_subtotal_and_hst_are_read():
    """"S. Total" and "13% HST" are the subtotal and tax; both were being missed."""
    ctx = parse_invoice([
        (1, "AEB Reference Quote# 50001502-6980 EXPEDITED S. Total: $ 1,128.00"),
        (1, "13% HST: $ 146.64"),
        (1, "Your contact: DRAKE Total: $ 1,274.64"),
    ])
    assert (ctx.subtotal, ctx.tax_amount, ctx.tax_rate_percent, ctx.total_amount) == (
        1128.00, 146.64, 13.0, 1274.64
    )


def test_rotated_page_is_re_read_at_the_orientation_that_reads_better():
    """A sideways photo yields confident nonsense; it must be re-read rotated."""
    from PIL import Image, ImageDraw

    from app.core.config import Settings
    from app.services.ocr_service import OcrService

    upright = Image.new("L", (1000, 600), color=255)
    draw = ImageDraw.Draw(upright)
    for i, line in enumerate(["TAX INVOICE", "VENDOR NAME LIMITED", "TOTAL AMOUNT DUE"]):
        draw.text((40, 60 + i * 90), line, fill=0)
    sideways = upright.rotate(90, expand=True)

    page = OcrService(Settings())._ocr_image(sideways, page_number=1)
    assert "INVOICE" in page.text.upper()


def test_upright_page_is_not_rotated():
    """The retry must not fire on a page that already read well."""
    from PIL import Image, ImageDraw

    from app.core.config import Settings
    from app.services.ocr_service import OcrService

    image = Image.new("L", (1000, 600), color=255)
    draw = ImageDraw.Draw(image)
    for i, line in enumerate(["TAX INVOICE", "VENDOR NAME LIMITED", "TOTAL AMOUNT DUE"]):
        draw.text((40, 60 + i * 90), line, fill=0)

    page = OcrService(Settings())._ocr_image(image, page_number=1)
    assert "INVOICE" in page.text.upper()


def test_customer_name_is_read_from_its_own_column():
    """"Sold To"/"Ship To" sit side by side; the buyer is under the left label."""
    ctx = parse_invoice([
        (1, "Vendu 4 - Sold To Livre a - Ship To"),
        (1, "Oz Optics Ltd."),
        (1, "Total 10.00"),
    ])
    assert ctx.customer_name == "Oz Optics Ltd."


def test_customer_account_header_is_not_a_customer_name():
    ctx = parse_invoice([
        (1, "CUSTOMER ACCOUNT CUSTOMER PO ORDER DATE"),
        (1, "10102028 292539 Jun 24 2021"),
    ])
    assert ctx.customer_name is None


def test_ocr_misread_quantity_prefix_still_yields_a_line_item():
    """OCR reads the "1" of "1x" as a letter; the item was being lost entirely."""
    ctx = parse_invoice([(1, "HASSIHO FINE WHOLEMEAL 420G"), (1, "ix 2.64 2.44 Z")])
    item = ctx.line_items[0]
    assert (item.quantity, item.unit_price, item.amount) == (1.0, 2.64, 2.44)


def test_quantity_row_below_a_description_that_carries_the_amount():
    """"973 COKE LIGHT RM4.40" / "#2 X RM 2,20" - amount above, quantity below."""
    ctx = parse_invoice([(1, "973 COKE LIGHT 500ML RM4.40"), (1, "#2 X RM 2,20")])
    item = ctx.line_items[0]
    assert (item.quantity, item.unit_price, item.amount) == (2.0, 2.20, 4.40)


def test_unresolvable_columns_keep_the_amount_but_report_no_quantity():
    """356.88 is printed plainly; which figure is the rate is not recoverable."""
    from app.utils.invoice_parsing import _parse_table_line_item

    parsed = _parse_table_line_item("6SPARKLE 200.GM BATI 48PCS. 24054000/24 PCS| 17.55 14.87 PCS 356.88")
    description, quantity, unit_price, amount = parsed
    assert amount == 356.88
    assert quantity is None and unit_price is None
    assert "SPARKLE" in description


def test_a_near_miss_is_still_reported_as_a_discrepancy():
    """Suppressing wide misses must not suppress genuine arithmetic errors."""
    from app.utils.invoice_parsing import _parse_table_line_item

    _, quantity, unit_price, amount = _parse_table_line_item("WIDGET 2 5.00 10.50")
    assert (quantity, unit_price, amount) == (2.0, 5.0, 10.5)


def test_line_item_check_is_not_applicable_without_a_quantity():
    from app.utils.invoice_parsing import InvoiceLineItem

    ctx = InvoiceContext()
    ctx.line_items = [InvoiceLineItem("Item", None, None, 356.88, 1, "row")]
    outcome = ExtractionOutcome({}, [], True, 1, {}, ctx)
    result = FinancialValidationService(get_settings()).validate(DocumentType.INVOICE, outcome)
    check = next(c for c in result.checks if c.name == "line_item_1_quantity_times_unit_price")
    assert check.status == ValidationStatus.NOT_APPLICABLE


def test_invoice_number_survives_ocr_colon_and_intervening_words():
    """"NO s 18291" is "NO: 18291"; "Invoice - Facture NO:" separates the words."""
    assert parse_invoice([(1, "INVOICE NO s 18291/102/70163")]).invoice_number == "18291/102/70163"
    assert parse_invoice([(1, "Invoice - Facture NO: 138236")]).invoice_number == "138236"


def test_tax_registration_and_item_counts_are_not_invoice_numbers():
    """The loose form only applies to a line that names an invoice."""
    assert parse_invoice([(1, "GST ID. NO s 000191747712")]).invoice_number is None
    assert parse_invoice([(1, "No of items: 2")]).invoice_number is None
