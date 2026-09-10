def test_health_check(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_process_rejects_unsupported_file_type(client):
    response = client.post(
        "/api/v1/documents/process",
        files={"file": ("notes.txt", b"plain text content", "text/plain")},
        data={"document_type": "invoice"},
    )
    assert response.status_code == 415
    body = response.json()
    assert body["error"]["code"] == "UNSUPPORTED_FILE_TYPE"


def test_process_rejects_empty_file(client):
    response = client.post(
        "/api/v1/documents/process",
        files={"file": ("empty.pdf", b"", "application/pdf")},
        data={"document_type": "invoice"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "EMPTY_FILE"


def test_process_flow_and_retrieval(client, blank_pdf_bytes):
    document_name = "test_balance_sheet.pdf"
    process_response = client.post(
        "/api/v1/documents/process",
        files={"file": (document_name, blank_pdf_bytes, "application/pdf")},
        data={"document_type": "balance_sheet"},
    )
    assert process_response.status_code == 200
    payload = process_response.json()

    assert payload["document_name"] == document_name
    assert payload["document_type"] == "balance_sheet"
    assert payload["processing_status"] in {"PASS", "FAILED"}
    assert payload["file_validation"]["status"] == "PASS"
    assert "extracted_data" in payload
    assert "validation" in payload
    assert "processing_metadata" in payload

    get_response = client.get(f"/api/v1/documents/{document_name}")
    assert get_response.status_code == 200
    assert get_response.json()["document_name"] == document_name

    list_response = client.get("/api/v1/documents")
    assert list_response.status_code == 200
    names = [doc["document_name"] for doc in list_response.json()["documents"]]
    assert document_name in names


def test_get_unknown_document_returns_404(client):
    response = client.get("/api/v1/documents/does-not-exist.pdf")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"
