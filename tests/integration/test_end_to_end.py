"""
End-to-end test — requires the full stack to be up (`make demo`).

Drives the ingest service with a real HL7 message, then polls HAPI FHIR
until the Patient shows up. A representative smoke test you can run before
every deploy as an acceptance gate.
"""
from __future__ import annotations

import os
import pathlib
import time

import httpx
import pytest

INGEST_URL = os.getenv("INGEST_URL", "http://localhost:8000/hl7")
FHIR_URL = os.getenv("FHIR_URL", "http://localhost:8080/fhir")
FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures" / "hl7"


def _stack_up() -> bool:
    try:
        r = httpx.get(INGEST_URL.replace("/hl7", "/healthz"), timeout=2.0)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(
    not _stack_up(), reason="stack not running; `make demo` first"
)


def test_adt_end_to_end() -> None:
    raw = (FIXTURES / "adt_a01.hl7").read_text().replace("\n", "\r")
    r = httpx.post(INGEST_URL, content=raw, headers={"Content-Type": "text/plain"})
    assert r.status_code == 200
    body = r.json()
    assert body["message_id"] == "MSG00001"

    # Poll for the Patient to show up in HAPI FHIR.
    deadline = time.time() + 20
    found = False
    while time.time() < deadline:
        q = httpx.get(
            f"{FHIR_URL}/Patient",
            params={"identifier": "https://fhir.nhs.uk/Id/nhs-number|9000000009"},
        )
        if q.status_code == 200 and q.json().get("total", 0) > 0:
            found = True
            break
        time.sleep(1)
    assert found, "Patient did not appear in HAPI FHIR within 20s"
