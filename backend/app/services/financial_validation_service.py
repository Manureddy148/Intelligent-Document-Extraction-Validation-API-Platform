from app.core.config import Settings
from app.core.logging import get_logger
from app.schemas.extraction import DocumentType, ValidationCheck, ValidationStatus, ValidationSummary
from app.services.extraction_service import ExtractionOutcome
from app.utils.invoice_parsing import InvoiceContext
from app.utils.text_parsing import StatementSection

logger = get_logger(__name__)


def _sum_optional(*values: float | None) -> float | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return sum(present)


def _sum_components(section: StatementSection | None, period: str) -> float | None:
    """Sum a section's component rows, excluding its reported total row.

    Returns None if any component row has no value for this period: summing the
    rows that happen to have been read would silently treat an unread row as
    zero and report a reconciliation failure that the document does not support.
    """
    if section is None:
        return None
    components = [item for item in section.items if "total" not in item.label.lower()]
    if not components:
        return None
    values = [item.values.get(period) for item in components]
    if any(value is None for value in values):
        return None
    return sum(values)


class FinancialValidationService:
    """Runs the per-document-type reconciliation checks required by the case study.

    Values come from the fields the extraction service already resolved, so the
    numbers checked here are exactly the numbers reported in `extracted_data`.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    def validate(self, document_type: DocumentType, outcome: ExtractionOutcome) -> ValidationSummary:
        if document_type == DocumentType.INVOICE:
            checks = self._validate_invoice(outcome.invoice_context)
        elif document_type == DocumentType.BALANCE_SHEET:
            checks = self._validate_balance_sheet(outcome)
        elif document_type == DocumentType.PROFIT_AND_LOSS:
            checks = self._validate_profit_and_loss(outcome)
        else:
            checks = self._validate_cash_flow(outcome)

        issues = [
            f"{check.name}: {check.message}" for check in checks if check.status == ValidationStatus.FAIL
        ]
        return ValidationSummary(
            checks=checks,
            overall_status=self._overall_status(checks),
            issues=issues,
        )

    def _overall_status(self, checks: list[ValidationCheck]) -> ValidationStatus:
        if any(check.status == ValidationStatus.FAIL for check in checks):
            return ValidationStatus.FAIL
        if any(check.status == ValidationStatus.PASS for check in checks):
            return ValidationStatus.PASS
        return ValidationStatus.NOT_APPLICABLE

    def _value(self, outcome: ExtractionOutcome, key: str, period: str) -> float | None:
        """Read an extracted field's value for a period (statements) or directly (invoices)."""
        field = outcome.extracted_data.get(key)
        if not isinstance(field, dict):
            return None
        value = field.get("value")
        if isinstance(value, dict):
            return value.get(period)
        return value if isinstance(value, (int, float)) else None

    def _compare(self, name: str, formula: str, operands: dict, calculated, reported) -> ValidationCheck:
        if calculated is None or reported is None:
            missing = [key for key, value in operands.items() if value is None]
            return ValidationCheck(
                name=name,
                formula=formula,
                operands=operands,
                calculated_value=calculated,
                reported_value=reported,
                variance=None,
                status=ValidationStatus.NOT_APPLICABLE,
                message=(
                    "Not all values required by this check are present in the document"
                    + (f" (missing: {', '.join(missing)})" if missing else "")
                    + "."
                ),
            )
        variance = round(calculated - reported, 2)
        tolerance = max(
            self.settings.financial_tolerance_absolute,
            self.settings.financial_tolerance_relative * max(abs(calculated), abs(reported), 1.0),
        )
        status = ValidationStatus.PASS if abs(variance) <= tolerance else ValidationStatus.FAIL
        return ValidationCheck(
            name=name,
            formula=formula,
            operands=operands,
            calculated_value=round(calculated, 2),
            reported_value=round(reported, 2),
            variance=variance,
            status=status,
            message=None
            if status == ValidationStatus.PASS
            else f"Variance {variance} exceeds tolerance {round(tolerance, 2)}.",
        )

    # ---- Invoice ----------------------------------------------------------

    def _validate_invoice(self, ctx: InvoiceContext | None) -> list[ValidationCheck]:
        checks: list[ValidationCheck] = []
        if ctx is None:
            return checks

        for index, item in enumerate(ctx.line_items, start=1):
            checks.append(
                self._compare(
                    name=f"line_item_{index}_quantity_times_unit_price",
                    formula="quantity * unit_price ≈ line amount",
                    operands={"quantity": item.quantity, "unit_price": item.unit_price},
                    calculated=round(item.quantity * item.unit_price, 2),
                    reported=item.amount,
                )
            )

        if ctx.line_items:
            line_sum = round(sum(item.amount for item in ctx.line_items), 2)
            reference = ctx.subtotal if ctx.subtotal is not None else ctx.total_amount
            checks.append(
                self._compare(
                    name="line_items_sum_reconciliation",
                    formula="sum(line item amounts) ≈ subtotal (or total when no subtotal is shown)",
                    operands={"line_items_sum": line_sum, "subtotal": ctx.subtotal, "total_amount": ctx.total_amount},
                    calculated=line_sum,
                    reported=reference,
                )
            )

        if ctx.subtotal is not None:
            checks.append(
                self._compare(
                    name="invoice_total_check",
                    formula="subtotal + tax_amount - discount ≈ total_amount",
                    operands={"subtotal": ctx.subtotal, "tax_amount": ctx.tax_amount, "discount": ctx.discount},
                    calculated=round(ctx.subtotal + (ctx.tax_amount or 0.0) - (ctx.discount or 0.0), 2),
                    reported=ctx.total_amount,
                )
            )
        elif ctx.tax_inclusive and ctx.tax_amount is not None and ctx.total_amount is not None:
            # GST/VAT already included in the printed total: check the implied
            # net-of-tax amount against the tax actually charged.
            net_of_tax = round(ctx.total_amount - ctx.tax_amount, 2)
            checks.append(
                self._compare(
                    name="tax_inclusive_total_check",
                    formula="(total_amount - tax_amount) + tax_amount ≈ total_amount (tax shown as included in total)",
                    operands={"total_amount": ctx.total_amount, "tax_amount": ctx.tax_amount, "net_of_tax": net_of_tax},
                    calculated=round(net_of_tax + ctx.tax_amount, 2),
                    reported=ctx.total_amount,
                )
            )
        else:
            checks.append(
                ValidationCheck(
                    name="invoice_total_check",
                    formula="subtotal + tax_amount - discount ≈ total_amount",
                    operands={"subtotal": ctx.subtotal, "tax_amount": ctx.tax_amount, "discount": ctx.discount},
                    calculated_value=None,
                    reported_value=ctx.total_amount,
                    variance=None,
                    status=ValidationStatus.NOT_APPLICABLE,
                    message="No subtotal is printed on this document, so the total cannot be recomputed from its parts.",
                )
            )

        if ctx.cash_paid is not None or ctx.change is not None:
            calculated = (
                round(ctx.cash_paid - ctx.total_amount, 2)
                if ctx.cash_paid is not None and ctx.total_amount is not None
                else None
            )
            checks.append(
                self._compare(
                    name="cash_change_check",
                    formula="cash_paid - total_amount ≈ change",
                    operands={"cash_paid": ctx.cash_paid, "total_amount": ctx.total_amount},
                    calculated=calculated,
                    reported=ctx.change,
                )
            )

        return checks

    # ---- Balance sheet ----------------------------------------------------

    def _validate_balance_sheet(self, outcome: ExtractionOutcome) -> list[ValidationCheck]:
        checks: list[ValidationCheck] = []
        for period in outcome.periods:
            total_liabilities = self._value(outcome, "total_liabilities", period)
            total_assets = self._value(outcome, "total_assets", period)

            checks.append(
                self._compare(
                    name=f"balance_sheet_equality[{period}]",
                    formula="Total Capital & Liabilities ≈ Total Assets",
                    operands={"total_capital_and_liabilities": total_liabilities, "total_assets": total_assets},
                    calculated=total_liabilities,
                    reported=total_assets,
                )
            )

            liabilities_components = _sum_components(outcome.sections.get("capital_and_liabilities"), period)
            checks.append(
                self._compare(
                    name=f"capital_and_liabilities_reconciliation[{period}]",
                    formula="sum(capital & liability line items) ≈ reported Total Capital & Liabilities",
                    operands={"component_sum": liabilities_components, "reported_total": total_liabilities},
                    calculated=liabilities_components,
                    reported=total_liabilities,
                )
            )

            asset_components = _sum_components(outcome.sections.get("assets"), period)
            checks.append(
                self._compare(
                    name=f"assets_reconciliation[{period}]",
                    formula="sum(asset line items) ≈ reported Total Assets",
                    operands={"component_sum": asset_components, "reported_total": total_assets},
                    calculated=asset_components,
                    reported=total_assets,
                )
            )
        return checks

    # ---- Profit & loss ----------------------------------------------------

    def _validate_profit_and_loss(self, outcome: ExtractionOutcome) -> list[ValidationCheck]:
        checks: list[ValidationCheck] = []
        for period in outcome.periods:
            interest_earned = self._value(outcome, "interest_earned", period)
            other_income = self._value(outcome, "other_income", period)
            total_income = self._value(outcome, "total_income", period)
            checks.append(
                self._compare(
                    name=f"total_income_check[{period}]",
                    formula="Interest Earned + Other Income ≈ Total Income",
                    operands={"interest_earned": interest_earned, "other_income": other_income},
                    calculated=_sum_optional(interest_earned, other_income)
                    if interest_earned is not None and other_income is not None
                    else None,
                    reported=total_income,
                )
            )

            interest_expended = self._value(outcome, "interest_expended", period)
            operating_expenses = self._value(outcome, "operating_expenses", period)
            provisions = self._value(outcome, "provisions_and_contingencies", period)
            total_expenditure = self._value(outcome, "total_expenditure", period)
            checks.append(
                self._compare(
                    name=f"total_expenditure_check[{period}]",
                    formula="Interest Expended + Operating Expenses + Provisions & Contingencies ≈ Total Expenditure",
                    operands={
                        "interest_expended": interest_expended,
                        "operating_expenses": operating_expenses,
                        "provisions_and_contingencies": provisions,
                    },
                    calculated=_sum_optional(interest_expended, operating_expenses, provisions)
                    if None not in (interest_expended, operating_expenses, provisions)
                    else None,
                    reported=total_expenditure,
                )
            )

            net_profit = self._value(outcome, "net_profit_for_the_year", period)
            checks.append(
                self._compare(
                    name=f"net_profit_before_minority_check[{period}]",
                    formula="Total Income - Total Expenditure ≈ Consolidated Net Profit before Minority Interest",
                    operands={"total_income": total_income, "total_expenditure": total_expenditure},
                    calculated=total_income - total_expenditure
                    if total_income is not None and total_expenditure is not None
                    else None,
                    reported=net_profit,
                )
            )

            minority = self._value(outcome, "minority_interest", period)
            associates = self._value(outcome, "share_in_profits_of_associates", period)
            attributable = self._value(outcome, "consolidated_profit_attributable_to_group", period)
            checks.append(
                self._compare(
                    name=f"net_profit_attributable_check[{period}]",
                    formula=(
                        "Net Profit before Minority Interest - Minority Interest + Share in Profits of Associates "
                        "≈ Consolidated Net Profit attributable to the Group"
                    ),
                    operands={
                        "net_profit_for_the_year": net_profit,
                        "minority_interest": minority,
                        "share_in_profits_of_associates": associates,
                    },
                    calculated=net_profit - minority + (associates or 0.0)
                    if net_profit is not None and minority is not None
                    else None,
                    reported=attributable,
                )
            )

            brought_forward = self._value(outcome, "brought_forward_profit", period)
            total_appropriation = self._value(outcome, "total_available_for_appropriation", period)
            checks.append(
                self._compare(
                    name=f"appropriation_check[{period}]",
                    formula="Current Profit + Brought Forward Profit ≈ Total Available for Appropriation",
                    operands={"current_profit": attributable, "brought_forward_profit": brought_forward},
                    calculated=_sum_optional(attributable, brought_forward)
                    if attributable is not None and brought_forward is not None
                    else None,
                    reported=total_appropriation,
                )
            )
        return checks

    # ---- Cash flow --------------------------------------------------------

    def _validate_cash_flow(self, outcome: ExtractionOutcome) -> list[ValidationCheck]:
        checks: list[ValidationCheck] = []
        for period in outcome.periods:
            operating = self._value(outcome, "operating_cash_flow", period)
            investing = self._value(outcome, "investing_cash_flow", period)
            financing = self._value(outcome, "financing_cash_flow", period)
            fx = self._value(outcome, "fx_translation_adjustment", period)
            net_change = self._value(outcome, "net_change_in_cash", period)

            checks.append(
                self._compare(
                    name=f"net_change_in_cash_check[{period}]",
                    formula=(
                        "Operating CF + Investing CF + Financing CF + FX/Translation Adjustment "
                        "≈ Net Increase in Cash & Cash Equivalents"
                    ),
                    operands={
                        "operating_cash_flow": operating,
                        "investing_cash_flow": investing,
                        "financing_cash_flow": financing,
                        "fx_translation_adjustment": fx,
                    },
                    calculated=operating + investing + financing + (fx or 0.0)
                    if None not in (operating, investing, financing)
                    else None,
                    reported=net_change,
                )
            )

            opening = self._value(outcome, "opening_cash", period)
            closing = self._value(outcome, "closing_cash", period)
            amalgamation = self._value(outcome, "cash_on_amalgamation_adjustment", period)
            checks.append(
                self._compare(
                    name=f"closing_cash_check[{period}]",
                    formula=(
                        "Opening Cash + Net Increase in Cash + Cash Acquired on Amalgamation/Other Adjustments "
                        "≈ Closing Cash"
                    ),
                    operands={
                        "opening_cash": opening,
                        "net_change_in_cash": net_change,
                        "cash_on_amalgamation_adjustment": amalgamation,
                    },
                    calculated=opening + net_change + (amalgamation or 0.0)
                    if opening is not None and net_change is not None
                    else None,
                    reported=closing,
                )
            )
        return checks
