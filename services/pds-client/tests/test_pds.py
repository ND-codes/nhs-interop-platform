"""Unit tests for the PDS client."""
from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import main


@pytest.fixture()
def stub_client(monkeypatch):
    monkeypatch.setattr(main, "PDS_STUB", True)
    main.cache.clear()
    with TestClient(main.app) as c:
        yield c


@pytest.fixture()
def live_client(monkeypatch):
    monkeypatch.setattr(main, "PDS_STUB", False)
    main.cache.clear()
    with TestClient(main.app) as c:
        yield c


def test_stub_returns_canned_record(stub_client: TestClient) -> None:
    r = stub_client.get("/pds/9000000009")
    assert r.status_code == 200
    assert r.json()["name"][0]["family"] == "Smith"


def test_rejects_invalid_format(stub_client: TestClient) -> None:
    r = stub_client.get("/pds/abc")
    assert r.status_code == 400


def test_rejects_short_number(stub_client: TestClient) -> None:
    r = stub_client.get("/pds/123")
    assert r.status_code == 400


@respx.mock
def test_live_mode_calls_sandbox(live_client: TestClient) -> None:
    respx.get(f"{main.PDS_BASE_URL}/Patient/9000000009").mock(
        return_value=httpx.Response(
            200, json={"resourceType": "Patient", "gender": "female"}
        )
    )
    r = live_client.get("/pds/9000000009")
    assert r.status_code == 200
    assert r.json()["gender"] == "female"


@respx.mock
def test_live_mode_handles_404(live_client: TestClient) -> None:
    respx.get(f"{main.PDS_BASE_URL}/Patient/9999999999").mock(
        return_value=httpx.Response(404)
    )
    r = live_client.get("/pds/9999999999")
    assert r.status_code == 404
