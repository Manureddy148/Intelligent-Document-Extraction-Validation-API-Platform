from app.core.config import Settings
from app.core.logging import get_logger
from app.schemas.extraction import DocumentType, ValidationCheck, ValidationStatus, ValidationSummary
from app.services.extraction_service import ExtractionOutcome
from app.utils.invoice_parsing import InvoiceContext
from app.utils.text_parsing import StatementLineItem, StatementSection

logger = get_logger(__name__)


def _item_value(item: StatementLineItem | None, period: str) -> float | None:
    return item.values.get(period) if item else None


def _sum_optional(*values: float | None) -> float | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return sum(present)


def _sum_section(section: StatementSection | None, exclude_key: str | None, period: str) -> float | None:
    if section is None:
        return None
    values = [
        item.values.get(period)
        for item in section.items
        if item.key != exclude_key and item.values.get(period) is not None
    ]
    if not values:
        return None
    return sum(values)


class FinancialValidationService:
    """Implements the per-document-type financial reconciliation checks from the case study spec."""

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

        overall_status = self._overall_status(checks)
        issues = [
            f"{check.name}: {check.message or 'variance exceeds tolerance'}"
            for check in checks
            if check.status == ValidationStatus.FAIL
        ]
        return ValidationSummary(checks=checks, overall_status=overall_status, issues=issues)

    def _overall_status(self, checks: list[ValidationCheck]) -> ValidationStatus:
        if any(c.status == ValidationStatus.FAIL for c in checks):
            return ValidationStatus.FAIL
        if any(c.status == ValidationStatus.PASS for c in checks):
            return ValidationStatus.PASS
        return ValidationStatus.NOT_APPLICABLE

    def _compare(self, name: str, formula: str, operands: dict, calculated, reported) -> ValidationCheck:
        if calculated is None or reported is None:
            return ValidationCheck(
                name=name,
                formula=formula,
                operands=operands,
                calculated_value=calculated,
                reported_value=reported,
                variance=None,
                status=ValidationStatus.NOT_APPLICABLE,
                message="Required field(s) for this check were not found in the document.",
            )
        variance = round(calculated - reported, 2)
        tolerance = max(
            self.settings.financial_tolerance_absolute,
            self.settings.financial_tolerance_relative * max(abs(calculated), abs(reported), 1.0),
        )
        status = ValidationStatus.PASS if abs(variance) <= tolerance else ValidationStatus.FAIL
        message = None if status == ValidationStatus.PASS else f"Variance {variance} exceeds tolerance {round(tolerance, 2)}."
        return ValidationCheck(
            name=name,
            formula=formula,
            operands=operands,
            calculated_value=round(calculated, 2),
            reported_value=round(reported, 2),
            variance=variance,
            status=status,
            message=message,
        )

    # ---- Invoice ----------------------------------------------------------

    def _validate_invoice(self, ctx: InvoiceContext | None) -> list[ValidationCheck]:
        checks: list[ValidationCheck] = []
        if ctx is None:
            return checks

        for index, item in enumerate(ctx.line_items, start=1):
            calculated = round(item.quantity * item.unit_price, 2)
            checks.append(
                self._compare(
                    name=f"line_item_{index}_quantity_times_price",
                    formula="quantity * unit_price ≈ amount",
                    operands={"quantity": item.quantity, "unit_price": item.unit_price},
                    calculated=calculated,
                    reported=item.amount,
                )
            )

        if ctx.line_items:
            line_total_sum = round(sum(li.amount for li in ctx.line_items), 2)
            reference_total = ctx.subtotal if ctx.subtotal is not None else ctx.total_amount
            checks.append(
                self._compare(
                    name="line_items_sum_reconciliation",
                    formula="sum(line_item.amount) ≈ subtotal (or total if no subtotal shown)",
                    operands={"line_items_sum": line_total_sum},
                    calculated=line_total_sum,
                    reported=reference_total,
                )
            )

        if ctx.subtotal is not None and ctx.tax_amount is not None:
            calculated = round(ctx.subtotal + ctx.tax_amount - (ctx.discount or 0.0), 2)
            checks.append(
                self._compare(
                    name="invoice_total_check",
                    formula="subtotal + tax_amount - discount ≈ total_amount",
                    operands={"subtotal": ctx.subtotal, "tax_amount": ctx.tax_amount, "discount": ctx.discount},
                    calculated=calculated,
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
                    message="Tax appears included in the displayed total, or subtotal was not separately printed.",
                )
            )

        if ctx.cash_paid is not None and ctx.total_amount is not None:
            calculated = round(ctx.cash_paid - ctx.total_amount, 2)
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

    # ---- Balance sheet ------------------------------------------------

    def _validate_balance_sheet(self, outcome: ExtractionOutcome) -> list[ValidationCheck]:
        checks: list[ValidationCheck] = []
        sections = outcome.sections
        cl_section = sections.get("capital_and_liabilities")
        assets_section = sections.get("assets")
        cl_total_item = cl_section.find("total") if cl_section else None
        assets_total_item = assets_section.find("total") if assets_section else None

        for period in outcome.periods:
            total_liabilities = _item_value(cl_total_item, period)
            total_assets = _item_value(assets_total_item, period)
            checks.append(
                self._compare(
                    name=f"balance_sheet_equality[{period}]",
                    formula="Total Capital & Liabilities ≈ Total Assets",
                    operands={"total_capital_and_liabilities": total_liabilities, "total_assets": total_assets},
                    calculated=total_liabilities,
                    reported=total_assets,
                )
            )

            cl_component_sum = _sum_section(cl_section, cl_total_item.key if cl_total_item else None, period)
            checks.append(
                self._compare(
                    name=f"capital_and_liabilities_reconciliation[{period}]",
                    formula="sum(capital & liability components) ≈ reported Total Capital & Liabilities",
                    operands={"component_sum": cl_component_sum, "reported_total": total_liabilities},
                    calculated=cl_component_sum,
                    reported=total_liabilities,
                )
            )

            assets_component_sum = _sum_section(assets_section, assets_total_item.key if assets_total_item else None, period)
            checks.append(
                self._compare(
                    name=f"assets_reconciliation[{period}]",
                    formula="sum(asset components) ≈ reported Total Assets",
                    operands={"component_sum": assets_component_sum, "reported_total": total_assets},
                    calculated=assets_component_sum,
                    reported=total_assets,
                )
            )

        return checks

    # ---- Profit & loss --------------------------------------------------

    def _validate_profit_and_loss(self, outcome: ExtractionOutcome) -> list[ValidationCheck]:
        checks: list[ValidationCheck] = []
        sections = outcome.sections
        income = sections.get("income")
        expenditure = sections.get("expenditure")
        profit = sections.get("profit")
        appropriations = sections.get("appropriations")

        interest_earned_item = income.find("interest earned") if income else None
        other_income_item = income.find("other income") if income else None
        total_income_item = income.find("total") if income else None

        interest_expended_item = expenditure.find("interest expended") if expenditure else None
        operating_expenses_item = expenditure.find("operating expenses") if expenditure else None
        provisions_item = expenditure.find("provisions and contingencies", "provisions & contingencies") if expenditure else None
        total_expenditure_item = expenditure.find("total") if expenditure else None

        net_profit_item = profit.find("net profit for the year") if profit else None
        minority_item = profit.find("minority interest") if profit else None
        associates_item = profit.find("share in profit") if profit else None
        attributable_item = profit.find("attributable to the group") if profit else None
        brought_forward_item = profit.find("brought forward") if profit else None

        total_appropriation_item = appropriations.find("total") if appropriations else None

        for period in outcome.periods:
            interest_earned = _item_value(interest_earned_item, period)
            other_income = _item_value(other_income_item, period)
            total_income = _item_value(total_income_item, period)
            calc_total_income = _sum_optional(interest_earned, other_income)
            checks.append(
                self._compare(
                    name=f"total_income_check[{period}]",
                    formula="Interest Earned + Other Income ≈ Total Income",
                    operands={"interest_earned": interest_earned, "other_income": other_income},
                    calculated=calc_total_income,
                    reported=total_income,
                )
            )

            interest_expended = _item_value(interest_expended_item, period)
            operating_expenses = _item_value(operating_expenses_item, period)
            provisions = _item_value(provisions_item, period)
            total_expenditure = _item_value(total_expenditure_item, period)
            calc_total_expenditure = _sum_optional(interest_expended, operating_expenses, provisions)
            checks.append(
                self._compare(
                    name=f"total_expenditure_check[{period}]",
                    formula="Interest Expended + Operating Expenses + Provisions & Contingencies ≈ Total Expenditure",
                    operands={
                        "interest_expended": interest_expended,
                        "operating_expenses": operating_expenses,
                        "provisions_and_contingencies": provisions,
                    },
                    calculated=calc_total_expenditure,
                    reported=total_expenditure,
                )
            )

            net_profit = _item_value(net_profit_item, period)
            calc_net_profit = (
                total_income - total_expenditure if total_income is not None and total_expenditure is not None else None
            )
            checks.append(
                self._compare(
                    name=f"net_profit_before_minority_check[{period}]",
                    formula="Total Income - Total Expenditure ≈ Consolidated Net Profit before Minority Interest",
                    operands={"total_income": total_income, "total_expenditure": total_expenditure},
                    calculated=calc_net_profit,
                    reported=net_profit,
                )
            )

            minority = _item_value(minority_item, period)
            associates = _item_value(associates_item, period)
            attributable = _item_value(attributable_item, period)
            calc_attributable = None
            if net_profit is not None and minority is not None:
                calc_attributable = net_profit - minority + (associates or 0.0)
            checks.append(
                self._compare(
                    name=f"net_profit_attributable_check[{period}]",
                    formula="Net Profit before Minority Interest - Minority Interest (+ Share in Associates) ≈ Consolidated Net Profit attributable to the Group",
                    operands={"net_profit": net_profit, "minority_interest": minority, "share_in_profits_of_associates": associates},
                    calculated=calc_attributable,
                    reported=attributable,
                )
            )

            brought_forward = _item_value(brought_forward_item, period)
            total_appropriation = _item_value(total_appropriation_item, period)
            calc_appropriation = _sum_optional(attributable, brought_forward)
            checks.append(
                self._compare(
                    name=f"appropriation_check[{period}]",
                    formula="Current Profit + Brought Forward Profit ≈ Total Available for Appropriation",
                    operands={"current_profit": attributable, "brought_forward_profit": brought_forward},
                    calculated=calc_appropriation,
                    reported=total_appropriation,
                )
            )

        return checks

    # ---- Cash flow --------------------------------------------------------

    def _validate_cash_flow(self, outcome: ExtractionOutcome) -> list[ValidationCheck]:
        checks: list[ValidationCheck] = []
        sections = outcome.sections
        operating = sections.get("operating")
        investing = sections.get("investing")
        financing = sections.get("financing")

        operating_item = operating.find("net cash flow", "net cash used in", "net cash generated") if operating else None
        investing_item = (
            investing.find("net cash used in investing", "net cash generated from investing", "net cash flow from investing")
            if investing
            else None
        )
        financing_item = (
            financing.find("net cash generated from financing", "net cash used in financing") if financing else None
        )
        fx_item = financing.find("exchange fluctuation", "translation reserve") if financing else None
        amalgamation_item = financing.find("amalgamation") if financing else None
        net_increase_item = financing.find("net increase", "net decrease in cash") if financing else None
        opening_item = financing.find("as at april", "opening") if financing else None
        closing_item = financing.find("as at march", "closing") if financing else None

        for period in outcome.periods:
            operating_cf = _item_value(operating_item, period)
            investing_cf = _item_value(investing_item, period)
            financing_cf = _item_value(financing_item, period)
            fx = _item_value(fx_item, period)
            net_increase = _item_value(net_increase_item, period)

            calc_net_increase = None
            if operating_cf is not None and investing_cf is not None and financing_cf is not None:
                calc_net_increase = operating_cf + investing_cf + financing_cf + (fx or 0.0)
            checks.append(
                self._compare(
                    name=f"net_change_in_cash_check[{period}]",
                    formula="Operating CF + Investing CF + Financing CF + FX/Translation Adjustment ≈ Net Increase in Cash",
                    operands={
                        "operating_cash_flow": operating_cf,
                        "investing_cash_flow": investing_cf,
                        "financing_cash_flow": financing_cf,
                        "fx_translation_adjustment": fx,
                    },
                    calculated=calc_net_increase,
                    reported=net_increase,
                )
            )

            opening_cash = _item_value(opening_item, period)
            closing_cash = _item_value(closing_item, period)
            amalgamation = _item_value(amalgamation_item, period)
            calc_closing = None
            if opening_cash is not None and net_increase is not None:
                calc_closing = opening_cash + net_increase + (amalgamation or 0.0)
            checks.append(
                self._compare(
                    name=f"closing_cash_check[{period}]",
                    formula="Opening Cash + Net Increase in Cash + Amalgamation/Other Adjustments ≈ Closing Cash",
                    operands={
                        "opening_cash": opening_cash,
                        "net_change_in_cash": net_increase,
                        "cash_on_amalgamation_adjustment": amalgamation,
                    },
                    calculated=calc_closing,
                    reported=closing_cash,
                )
            )

        return checks
