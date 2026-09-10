import io

from app.models.schemas import DocumentType


def test_extract_success(client):
    content = b"Invoice INV-2024-01\nDate: 01/02/2024\nTotal: $250.00"
    response = client.post(
        "/api/v1/documents/extract",
        files={"file": ("invoice.txt", io.BytesIO(content), "text/plain")},
        data={"document_type": DocumentType.INVOICE.value},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["document_type"] == "invoice"
    field_names = {f["name"] for f in body["fields"]}
    assert "date" in field_names
    assert "amount" in field_names


def test_extract_rejects_unsupported_content_type(client):
    response = client.post(
        "/api/v1/documents/extract",
        files={"file": ("data.exe", io.BytesIO(b"binary"), "application/octet-stream")},
        data={"document_type": DocumentType.GENERIC.value},
    )
    assert response.status_code == 415


def test_validate_endpoint(client):
    response = client.post(
        "/api/v1/documents/validate",
        json={
            "document_type": "invoice",
            "fields": {
                "invoice_number": "INV-100",
                "date": "01/02/2024",
                "amount": "$99.99",
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["is_valid"] is True


def test_process_endpoint(client):
    content = b"Invoice INV-2024-01\nDate: 01/02/2024\nTotal: $250.00"
    response = client.post(
        "/api/v1/documents/process",
        files={"file": ("invoice.txt", io.BytesIO(content), "text/plain")},
        data={"document_type": DocumentType.INVOICE.value},
    )
    assert response.status_code == 200
    body = response.json()
    assert "extraction" in body
    assert "validation" in body
