from app.models.schemas import DocumentType
from app.services.validation import DocumentValidator


def test_invoice_missing_required_fields():
    validator = DocumentValidator()
    result = validator.validate(DocumentType.INVOICE, {})
    assert not result.is_valid
    missing = {issue.field for issue in result.issues}
    assert {"invoice_number", "date", "amount"} <= missing


def test_invoice_valid_fields():
    validator = DocumentValidator()
    result = validator.validate(
        DocumentType.INVOICE,
        {"invoice_number": "INV-1001", "date": "12/05/2024", "amount": "$100.00"},
    )
    assert result.is_valid
    assert result.issues == []


def test_invalid_date_format():
    validator = DocumentValidator()
    result = validator.validate(DocumentType.RECEIPT, {"date": "not-a-date", "amount": "50"})
    assert not result.is_valid
    assert any(issue.field == "date" for issue in result.issues)
