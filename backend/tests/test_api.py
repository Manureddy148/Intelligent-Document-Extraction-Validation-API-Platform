def test_health_check(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["ocr_engine"].startswith("tesseract")


def test_health_check_reports_503_when_the_database_is_unreachable(client):
    """Reporting "ok" with a dead database keeps a broken instance in the pool."""
    from app.api.routes import health

    class BrokenSession:
        def execute(self, *args, **kwargs):
            raise RuntimeError("connection refused")

    app = client.app
    app.dependency_overrides[health.get_db] = lambda: BrokenSession()
    try:
        response = client.get("/api/v1/health")
    finally:
        app.dependency_overrides.pop(health.get_db, None)

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["database"] == "unavailable"


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


def test_list_rejects_out_of_range_paging(client):
    """Unbounded paging parameters are input the API should refuse, not attempt."""
    assert client.get("/api/v1/documents?limit=0").status_code == 422
    assert client.get("/api/v1/documents?limit=9999").status_code == 422
    assert client.get("/api/v1/documents?offset=-1").status_code == 422
    assert client.get("/api/v1/documents?limit=10&offset=0").status_code == 200


def test_openapi_documents_response_models_and_error_envelopes(client):
    """Swagger must show the response shape and the errors a caller can receive."""
    schema = client.get("/openapi.json").json()

    def ok_ref(path, method):
        content = schema["paths"][path][method]["responses"]["200"]["content"]
        return content["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]

    assert ok_ref("/api/v1/documents/process", "post") == "ProcessingResult"
    assert ok_ref("/api/v1/documents/{document_name}", "get") == "ProcessingResult"
    assert ok_ref("/api/v1/documents", "get") == "DocumentListResponse"
    assert ok_ref("/api/v1/health", "get") == "HealthResponse"

    upload_errors = schema["paths"]["/api/v1/documents/process"]["post"]["responses"]
    for code in ("400", "413", "415", "422"):
        assert code in upload_errors, f"{code} not documented on the upload endpoint"
    assert "404" in schema["paths"]["/api/v1/documents/{document_name}"]["get"]["responses"]


def test_unsupported_type_is_detected_from_content_not_extension(client):
    """A text file renamed .pdf must still be rejected."""
    response = client.post(
        "/api/v1/documents/process",
        files={"file": ("disguised.pdf", b"just plain text", "application/pdf")},
        data={"document_type": "invoice"},
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"


def test_uploaded_file_names_are_reduced_to_a_base_name(client, blank_pdf_bytes):
    """The caller picks this name and it becomes a database key and a URL."""
    response = client.post(
        "/api/v1/documents/process",
        files={"file": ("../../../etc/passwd", blank_pdf_bytes, "application/pdf")},
        data={"document_type": "invoice"},
    )
    assert response.status_code == 200
    assert response.json()["document_name"] == "passwd"


def test_every_error_uses_one_envelope(client):
    """A client must not have to parse two error shapes."""
    # FastAPI request validation
    invalid = client.post(
        "/api/v1/documents/process",
        files={"file": ("x.pdf", b"%PDF-1.4", "application/pdf")},
        data={"document_type": "not_a_type"},
    )
    assert invalid.status_code == 422
    assert set(invalid.json()["error"]) == {"code", "message"}

    # Starlette's own 404 for a path that matches no route
    unrouted = client.get("/api/v1/nope")
    assert unrouted.status_code == 404
    assert unrouted.json()["error"]["code"] == "NOT_FOUND"

    # An application error
    missing = client.get("/api/v1/documents/never-processed.pdf")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"


def test_reprocessing_a_name_replaces_rather_than_duplicates(client, blank_pdf_bytes):
    for _ in range(3):
        client.post(
            "/api/v1/documents/process",
            files={"file": ("repeat.pdf", blank_pdf_bytes, "application/pdf")},
            data={"document_type": "invoice"},
        )
    listing = client.get("/api/v1/documents?limit=100").json()
    assert [d["document_name"] for d in listing["documents"]].count("repeat.pdf") == 1


def test_non_numeric_recovered_values_never_reach_numeric_fields(client, blank_pdf_bytes, monkeypatch):
    """A model returning "10%" for a rate must not become a 500 later."""
    from app.services.document_service import DocumentService
    from app.schemas.extraction import DocumentType

    service = DocumentService.__new__(DocumentService)
    from app.utils.invoice_parsing import InvoiceContext
    from app.services.extraction_service import ExtractionOutcome

    ctx = InvoiceContext()
    outcome = ExtractionOutcome({}, ["current"], True, 1, {}, ctx)
    service._apply_to_invoice_context(
        outcome,
        DocumentType.INVOICE,
        {
            "tax_rate_percent": {"value": "10%"},     # string into a numeric field
            "subtotal": {"value": 126.27},            # good
            "vendor_name": {"value": "ACME Ltd"},     # good
            "total_amount": {"value": "RM 9.00"},     # string into a numeric field
        },
    )
    assert ctx.tax_rate_percent is None
    assert ctx.total_amount is None
    assert ctx.subtotal == 126.27
    assert ctx.vendor_name == "ACME Ltd"


def test_recovered_tax_cannot_exceed_the_amount_payable():
    """A receipt came back with its grand total recovered as the tax line."""
    from app.services.document_service import DocumentService
    from app.schemas.extraction import DocumentType
    from app.services.extraction_service import ExtractionOutcome
    from app.utils.invoice_parsing import InvoiceContext

    service = DocumentService.__new__(DocumentService)
    ctx = InvoiceContext()
    ctx.total_amount = 165.0
    outcome = ExtractionOutcome({}, ["current"], True, 1, {}, ctx)
    service._apply_to_invoice_context(
        outcome, DocumentType.INVOICE,
        {"tax_amount": {"value": 165.0}, "subtotal": {"value": 105.0}},
    )
    assert ctx.tax_amount is None      # cannot be the whole bill
    assert ctx.subtotal == 105.0       # legitimately below it
