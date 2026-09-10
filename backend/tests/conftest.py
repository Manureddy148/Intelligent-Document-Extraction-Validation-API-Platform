import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["IDEV_DATABASE_URL"] = f"sqlite:///{_tmp_db.name}"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.database import init_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _init_database():
    init_db()
    yield


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def blank_pdf_bytes() -> bytes:
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()
