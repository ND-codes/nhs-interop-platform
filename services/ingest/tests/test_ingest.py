"""Unit tests for the ingest service."""
from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import main


@pytest.fixture()
def client():
    with TestClient(main.app) as c:
        yield c


VALID_ADT = (
    "MSH|^~\\&|CERNER|GENERAL_HOSP|INTEROP|GENERAL_HOSP|20260101120000||"
    "ADT^A01|MSG00001|P|2.5\r"
    "EVN|A01|20260101120000\r"
    "PID|1||9000000009^^^NHS^NH||DOE^JOHN^A||19800101|M|||"
    "1 NHS WAY^^LONDON^^SW1A 1AA^GBR\r"
    "PV1|1|I|WARD1^BED1^ROOM1||||12345^SMITH^JANE|||GEN\r"
)


def test_healthz(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["service"] == "ingest"


def test_readyz(client: TestClient) -> None:
    r = client.get("/readyz")
    assert r.status_code == 200


def test_rejects_empty_body(client: TestClient) -> None:
    r = client.post("/hl7", content=b"")
    assert r.status_code == 400


def test_rejects_missing_msh(client: TestClient) -> None:
    r = client.post("/hl7", content=b"PID|1|||DOE^JOHN")
    assert r.status_code == 400
    assert "MSH" in r.json()["detail"]


@respx.mock
def test_forwards_valid_message(client: TestClient) -> None:
    route = respx.post(main.TRANSFORM_URL).mock(
        return_value=httpx.Response(200, json={"fhir_id": "Patient/1"})
    )
    r = client.post("/hl7", content=VALID_ADT.encode())
    assert r.status_code == 200
    body = r.json()
    assert body["message_id"] == "MSG00001"
    assert body["forwarded"] is True
    assert route.called


@respx.mock
def test_accepts_even_when_transform_down(client: TestClient) -> None:
    respx.post(main.TRANSFORM_URL).mock(side_effect=httpx.ConnectError("boom"))
    r = client.post("/hl7", content=VALID_ADT.encode())
    assert r.status_code == 200
    assert r.json()["forwarded"] is False
