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


def test_dashboard_page_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Process a document" in response.text
    assert "/static/js/dashboard.js" in response.text


def test_document_result_page_renders(client):
    response = client.get("/documents/example.pdf")
    assert response.status_code == 200
    assert 'data-document-name="example.pdf"' in response.text


def test_static_assets_are_served(client):
    assert client.get("/static/css/styles.css").status_code == 200
    assert client.get("/static/js/dashboard.js").status_code == 200


def test_openapi_schema_documents_the_required_endpoints(client):
    schema = client.get("/openapi.json").json()
    for path in ["/api/v1/documents/process", "/api/v1/documents", "/api/v1/documents/{document_name}", "/api/v1/health"]:
        assert path in schema["paths"], f"{path} missing from OpenAPI schema"
