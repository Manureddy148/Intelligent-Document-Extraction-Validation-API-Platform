import re

from app.models.schemas import DocumentType, ValidationIssue, ValidationResult

_REQUIRED_FIELDS: dict[DocumentType, list[str]] = {
    DocumentType.INVOICE: ["invoice_number", "date", "amount"],
    DocumentType.RECEIPT: ["date", "amount"],
    DocumentType.ID_CARD: ["id_number"],
    DocumentType.GENERIC: [],
}

_FIELD_FORMATS: dict[str, str] = {
    "date": r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}",
    "amount": r"(?:USD|INR|\$|₹)?\s?\d+(?:,\d{3})*(?:\.\d{1,2})?",
    "email": r"[\w.+-]+@[\w-]+\.[\w.-]+",
}


class DocumentValidator:
    """Validates extracted fields against per-document-type rules."""

    def validate(self, document_type: DocumentType, fields: dict[str, str]) -> ValidationResult:
        issues: list[ValidationIssue] = []

        for required in _REQUIRED_FIELDS.get(document_type, []):
            if not fields.get(required):
                issues.append(ValidationIssue(field=required, message="Required field is missing"))

        for name, value in fields.items():
            pattern = _FIELD_FORMATS.get(name)
            if pattern and value and not re.fullmatch(pattern, value.strip(), flags=re.IGNORECASE):
                issues.append(
                    ValidationIssue(field=name, message=f"Value '{value}' does not match the expected format")
                )

        return ValidationResult(
            document_type=document_type,
            is_valid=not issues,
            issues=issues,
        )
