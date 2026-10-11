"""Dead-letter path for FHIR write failures.

A write that still fails after the retries must leave the raw message in the
dead-letter folder, add one to hl7_dlq_total and answer 502. HAPI FHIR is
mocked with respx, so the test needs no network or running stack.
"""
from __future__ import annotations

import os
import tempfile

# main.py creates DLQ_PATH when it is imported, so point it somewhere
# disposable before the import.
os.environ.setdefault("DLQ_PATH", tempfile.mkdtemp(prefix="dlq-"))

import httpx  # noqa: E402
import respx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from prometheus_client import REGISTRY  # noqa: E402

import main  # noqa: E402

FHIR_URL = "http://hapi.test/fhir"

ADT_A01 = (
    "MSH|^~\\&|CERNER|GENERAL_HOSP|INTEROP|GENERAL_HOSP|20260101120000||"
    "ADT^A01|MSG00001|P|2.5\r"
    "EVN|A01|20260101120000\r"
    "PID|1||9000000009^^^NHS^NH~L12345^^^GENERAL_HOSP^MR||DOE^JOHN^A||"
    "19800101|M|||1 NHS WAY^^LONDON^^SW1A 1AA^GBR\r"
    "PV1|1|I|WARD1^BED1^ROOM1||||12345^SMITH^JANE|||GEN\r"
)


def _dlq_count() -> float:
    value = REGISTRY.get_sample_value("hl7_dlq_total", {"reason": "fhir_write_error"})
    return value or 0.0


def test_failed_fhir_write_is_dead_lettered(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(main, "DLQ_PATH", tmp_path)
    monkeypatch.setattr(main, "PDS_ENABLED", False)
    monkeypatch.setattr(main, "FHIR_BASE_URL", FHIR_URL)
    before = _dlq_count()

    with respx.mock(assert_all_called=True) as hapi:
        route = hapi.post(FHIR_URL).mock(return_value=httpx.Response(503))
        with TestClient(main.app) as client:
            response = client.post(
                "/transform",
                content=ADT_A01,
                headers={"X-Trace-Id": "write-fails"},
            )

    assert route.call_count == 3  # the retries ran before giving up
    assert response.status_code == 502
    assert _dlq_count() == before + 1

    # Read the file back as bytes. Reading it as text would turn the HL7
    # segment terminators into line feeds and hide whether they survived.
    written = (tmp_path / "write-fails.hl7").read_bytes()
    assert written == ADT_A01.encode("utf-8")
    assert b"\n" not in written
