"""The NHS number must not reach a log record, a metric label, or an outbound
correlation header.

These tests assert against the formatter's own output rather than caplog,
because the defect being guarded is a serialisation one: the format string
does not name `nhs_number`, but python-json-logger serialises everything in
`extra` regardless, so a line that looks clean in the source still carries the
identifier to stdout. caplog inspects the record, not the bytes, and would
have passed while the leak continued.
"""
from __future__ import annotations

import io
import json
import logging

import httpx
import pybreaker
import pytest
import respx
from fastapi.testclient import TestClient
from pythonjsonlogger import jsonlogger

import main

# A synthetic number that passes the Modulus 11 check digit. Not a real
# patient; the NHS PDS sandbox publishes this range for testing.
NHS_NUMBER = "9000000009"
TRACE_ID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def captured_log() -> io.StringIO:
    """Attach the service's real formatter to a buffer, so the test sees the
    exact bytes the container would write to stdout."""
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(
        jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    main.logger.addHandler(handler)
    try:
        yield buffer
    finally:
        main.logger.removeHandler(handler)


@pytest.fixture(autouse=True)
def clean_state():
    main.cache.clear()
    main.breaker.close()
    yield
    main.cache.clear()
    main.breaker.close()


def records(buffer: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in buffer.getvalue().splitlines() if line.strip()]


def test_a_cache_hit_does_not_log_the_nhs_number(captured_log, monkeypatch) -> None:
    monkeypatch.setattr(main, "PDS_STUB", True)
    with TestClient(main.app) as client:
        client.get(f"/pds/{NHS_NUMBER}", headers={"X-Trace-Id": TRACE_ID})
        client.get(f"/pds/{NHS_NUMBER}", headers={"X-Trace-Id": TRACE_ID})

    messages = [r["message"] for r in records(captured_log)]
    assert any("cache hit" in m for m in messages), "the cache path did not run"
    assert NHS_NUMBER not in captured_log.getvalue()


def test_the_stub_path_does_not_log_the_nhs_number(captured_log, monkeypatch) -> None:
    monkeypatch.setattr(main, "PDS_STUB", True)
    with TestClient(main.app) as client:
        resp = client.get(f"/pds/{NHS_NUMBER}", headers={"X-Trace-Id": TRACE_ID})
    assert resp.status_code == 200
    assert NHS_NUMBER not in captured_log.getvalue()


def test_an_upstream_error_does_not_log_the_nhs_number(captured_log, monkeypatch) -> None:
    monkeypatch.setattr(main, "PDS_STUB", False)
    with TestClient(main.app) as client, respx.mock:
        respx.get(url__startswith=main.PDS_BASE_URL).mock(
            side_effect=httpx.ConnectError("sandbox unreachable")
        )
        resp = client.get(f"/pds/{NHS_NUMBER}", headers={"X-Trace-Id": TRACE_ID})

    assert resp.status_code == 502
    messages = [r["message"] for r in records(captured_log)]
    assert any("upstream error" in m for m in messages), "the error path did not run"
    assert NHS_NUMBER not in captured_log.getvalue()


@pytest.mark.parametrize("status", [500, 429])
def test_an_upstream_status_error_does_not_leak_the_nhs_number(
    captured_log, monkeypatch, status
) -> None:
    """The test above uses a connection error, whose message has no URL in it.
    A real error response raises HTTPStatusError, and httpx builds that
    message from the request URL, which ends in /Patient/<nhs_number>. This
    is the case that leaked through str(exc) after the first fix."""
    monkeypatch.setattr(main, "PDS_STUB", False)
    with TestClient(main.app) as client, respx.mock:
        respx.get(url__startswith=main.PDS_BASE_URL).mock(
            return_value=httpx.Response(status)
        )
        resp = client.get(f"/pds/{NHS_NUMBER}", headers={"X-Trace-Id": TRACE_ID})

    assert resp.status_code == 502
    logged = [r for r in records(captured_log) if "upstream error" in r["message"]]
    assert logged, "the error path did not run"
    assert logged[0]["upstream_status"] == status
    assert logged[0]["trace_id"] == TRACE_ID
    assert NHS_NUMBER not in captured_log.getvalue()
    assert NHS_NUMBER not in resp.text, "the 502 detail goes back to transform"


def test_an_open_breaker_does_not_log_the_nhs_number(captured_log, monkeypatch) -> None:
    monkeypatch.setattr(main, "PDS_STUB", False)
    main.breaker.open()
    with TestClient(main.app) as client:
        resp = client.get(f"/pds/{NHS_NUMBER}", headers={"X-Trace-Id": TRACE_ID})

    assert resp.status_code == 503
    messages = [r["message"] for r in records(captured_log)]
    assert any("circuit open" in m for m in messages), "the breaker path did not run"
    assert NHS_NUMBER not in captured_log.getvalue()


def test_the_logged_identifier_is_the_trace_id_transform_sent(
    captured_log, monkeypatch
) -> None:
    monkeypatch.setattr(main, "PDS_STUB", True)
    with TestClient(main.app) as client:
        client.get(f"/pds/{NHS_NUMBER}", headers={"X-Trace-Id": TRACE_ID})

    assert any(r.get("trace_id") == TRACE_ID for r in records(captured_log))


def test_a_missing_trace_id_does_not_fall_back_to_the_patient(
    captured_log, monkeypatch
) -> None:
    """transform always sends the header, but pds-client is reachable inside
    the cluster by anything the NetworkPolicy allows, so the fallback matters.
    It must be a generated id, never the NHS number."""
    monkeypatch.setattr(main, "PDS_STUB", True)
    with TestClient(main.app) as client:
        client.get(f"/pds/{NHS_NUMBER}")

    logged = records(captured_log)
    assert any(r.get("trace_id") for r in logged), "nothing was correlated at all"
    assert NHS_NUMBER not in captured_log.getvalue()


def test_the_outbound_request_id_is_the_trace_id_not_the_patient(monkeypatch) -> None:
    """X-Request-ID is echoed by upstreams and recorded by intermediaries, so
    it leaks further than our own log store."""
    monkeypatch.setattr(main, "PDS_STUB", False)
    seen: dict[str, str] = {}

    def record_headers(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={"resourceType": "Patient"})

    with TestClient(main.app) as client, respx.mock:
        respx.get(url__startswith=main.PDS_BASE_URL).mock(side_effect=record_headers)
        resp = client.get(f"/pds/{NHS_NUMBER}", headers={"X-Trace-Id": TRACE_ID})

    assert resp.status_code == 200
    assert seen["x-request-id"] == TRACE_ID
    assert seen["x-request-id"] != NHS_NUMBER


def test_the_breaker_opens_when_the_sandbox_keeps_failing(monkeypatch) -> None:
    """The same defect Part 4 fixed in transform was still here: @breaker on a
    coroutine function counts the returned coroutine object as a success, so
    the breaker never opened however many calls failed."""
    monkeypatch.setattr(main, "PDS_STUB", False)
    with TestClient(main.app) as client, respx.mock:
        respx.get(url__startswith=main.PDS_BASE_URL).mock(
            side_effect=httpx.ConnectError("sandbox unreachable")
        )
        for _ in range(main.breaker.fail_max):
            main.cache.clear()
            client.get(f"/pds/{NHS_NUMBER}", headers={"X-Trace-Id": TRACE_ID})

    assert main.breaker.current_state == pybreaker.STATE_OPEN
    assert main.breaker.fail_counter >= main.breaker.fail_max
