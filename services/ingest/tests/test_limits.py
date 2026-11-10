"""POST /hl7 reads raw bytes, because an HL7 v2 message is not JSON, so no
Pydantic model bounds it. These tests pin the size limit that replaces one.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

import main

VALID = (
    "MSH|^~\\&|EPIC|RGT|INTEROP|RGT|20261109120000||ADT^A01|MSG00001|P|2.5.1\r"
    "EVN|A01|20261109120000\r"
    "PID|1||9000000009^^^NHS^NH||DOE^JOHN^A||19800101|M\r"
)


def test_a_normal_message_is_accepted() -> None:
    with TestClient(main.app) as client:
        resp = client.post("/hl7", content=VALID.encode("utf-8"))
    assert resp.status_code in (200, 202), resp.text


def test_a_body_over_the_limit_is_rejected_with_413(monkeypatch) -> None:
    monkeypatch.setattr(main, "MAX_BODY_BYTES", 512)
    oversized = VALID + ("OBX|1|ST|PAD||" + "x" * 2000 + "\r")
    with TestClient(main.app) as client:
        resp = client.post("/hl7", content=oversized.encode("utf-8"))
    assert resp.status_code == 413
    assert "limit" in resp.json()["detail"]


def test_the_limit_is_enforced_on_the_stream_not_just_content_length(
    monkeypatch,
) -> None:
    """Content-Length is a claim the client makes, and a chunked request need
    not send one, so the byte count has to be enforced while reading."""
    monkeypatch.setattr(main, "MAX_BODY_BYTES", 512)

    def chunked():
        yield VALID.encode("utf-8")
        for _ in range(10):
            yield b"x" * 256

    with TestClient(main.app) as client:
        resp = client.post("/hl7", content=chunked())
    assert resp.status_code == 413


def test_a_body_exactly_at_the_limit_is_still_accepted(monkeypatch) -> None:
    """Off-by-one guard: the limit is the largest body allowed, not the
    smallest body refused."""
    body = VALID.encode("utf-8")
    monkeypatch.setattr(main, "MAX_BODY_BYTES", len(body))
    with TestClient(main.app) as client:
        resp = client.post("/hl7", content=body)
    assert resp.status_code in (200, 202), resp.text


def test_an_empty_body_is_still_a_400_not_a_413() -> None:
    with TestClient(main.app) as client:
        resp = client.post("/hl7", content=b"")
    assert resp.status_code == 400
