#!/usr/bin/env python3
"""Process every document in the sample set against a running instance.

Populates the dashboard and prints what came back, so a deployment can be
checked end to end rather than one upload at a time.

    python scripts/load_sample_documents.py https://your-service.onrender.com

Reads `case_study/sample_documents.zip` directly - nothing to unpack first.
Folder names in the archive give each file its document type. Uses only the
standard library, so it runs anywhere Python does.
"""

import argparse
import io
import json
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from collections import Counter
from pathlib import Path

# Folder name in the archive -> the document_type the API expects.
FOLDER_TYPES = {
    "balance sheet": "balance_sheet",
    "profit & loss": "profit_and_loss",
    "cash flows": "cash_flow_statement",
    "invoices": "invoice",
}
CONTENT_TYPES = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
DEFAULT_ZIP = Path(__file__).resolve().parent.parent / "case_study" / "sample_documents.zip"


def document_type_for(name: str) -> str | None:
    for folder, doc_type in FOLDER_TYPES.items():
        if f"/{folder}/" in name.lower():
            return doc_type
    return None


def post(base: str, name: str, payload: bytes, doc_type: str, timeout: int):
    boundary = uuid.uuid4().hex
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
        f'Content-Type: {CONTENT_TYPES.get(Path(name).suffix.lower(), "application/octet-stream")}\r\n\r\n'
    ).encode()
    body += payload + (
        f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="document_type"\r\n\r\n'
        f"{doc_type}\r\n--{boundary}--\r\n"
    ).encode()

    request = urllib.request.Request(
        f"{base}/api/v1/documents/process",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read()), time.perf_counter() - started
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read()), time.perf_counter() - started
        except Exception:
            return exc.code, {}, time.perf_counter() - started
    except Exception as exc:  # network/timeout
        return None, {"error": {"message": f"{type(exc).__name__}: {exc}"}}, time.perf_counter() - started


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("base_url", help="e.g. https://your-service.onrender.com")
    parser.add_argument("--archive", type=Path, default=DEFAULT_ZIP)
    parser.add_argument("--timeout", type=int, default=300, help="per-document seconds (default 300)")
    parser.add_argument("--limit", type=int, help="process only the first N documents")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    if not args.archive.exists():
        print(f"archive not found: {args.archive}", file=sys.stderr)
        return 2

    # A free instance sleeps when idle; the first request pays the wake-up.
    print(f"Waking {base} ...", flush=True)
    try:
        with urllib.request.urlopen(f"{base}/api/v1/health", timeout=180) as response:
            print("  health:", response.read().decode()[:140])
    except Exception as exc:
        print(f"  could not reach the health endpoint: {exc}", file=sys.stderr)
        return 2

    with zipfile.ZipFile(args.archive) as archive:
        entries = [
            info for info in archive.infolist()
            if not info.is_dir()
            and Path(info.filename).suffix.lower() in CONTENT_TYPES
            and document_type_for(info.filename)
            and not Path(info.filename).name.startswith(".")
        ]
        entries.sort(key=lambda info: (document_type_for(info.filename), info.filename))
        if args.limit:
            entries = entries[: args.limit]

        print(f"\n{len(entries)} documents\n")
        header = f"{'document':<46}{'type':<21}{'http':<6}{'status':<8}{'validation':<16}{'secs':>6}"
        print(header)
        print("-" * len(header))

        checks, statuses, timings, failures = Counter(), Counter(), [], []
        for info in entries:
            doc_type = document_type_for(info.filename)
            name = Path(info.filename).name
            code, body, elapsed = post(base, name, archive.read(info), doc_type, args.timeout)
            timings.append(elapsed)

            if code != 200:
                message = body.get("error", {}).get("message", "")
                print(f"{name[:44]:<46}{doc_type:<21}{str(code):<6}{'ERROR':<8}{message[:30]:<16}{elapsed:>6.1f}")
                statuses["ERROR"] += 1
                failures.append((name, message))
                continue

            validation = body["validation"]
            checks.update(check["status"] for check in validation["checks"])
            statuses[body["processing_status"]] += 1
            print(
                f"{name[:44]:<46}{doc_type:<21}{code:<6}{body['processing_status']:<8}"
                f"{validation['overall_status']:<16}{elapsed:>6.1f}"
            )

    print("-" * len(header))
    print("documents:", dict(statuses))
    print("checks:   ", {k: checks[k] for k in ("PASS", "FAIL", "NOT_APPLICABLE") if checks[k]})
    if timings:
        print(f"timing:    median {sorted(timings)[len(timings)//2]:.1f}s  max {max(timings):.1f}s")

    with urllib.request.urlopen(f"{base}/api/v1/documents?limit=500", timeout=120) as response:
        listing = json.loads(response.read())
    print(f"dashboard: {listing['total']} documents stored")
    print(f"\nOpen {base}/ to browse the results.")

    if failures:
        print("\nfailed uploads:", file=sys.stderr)
        for name, message in failures:
            print(f"  {name}: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
