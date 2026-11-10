"""A failed PDS lookup must not put the NHS number in transform's logs.

transform calls PDS_CLIENT_URL/<nhs_number>. When pds-client answers with an
error, httpx raises HTTPStatusError, and its message is built from the request
URL. Logging str(exc) therefore logged the identifier on every failed lookup.
Asserted against the formatter's output, as in pds-client, because the leak is
in what reaches stdout, not in what the log record looks like.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging

import httpx
import pytest
import respx
from pythonjsonlogger import jsonlogger

import main

NHS_NUMBER = "9000000009"
TRACE_ID = "11111111-2222-3333-4444-555555555555"
PATIENT = {
    "resourceType": "Patient",
    "identifier": [
        {"system": "https://fhir.nhs.uk/Id/nhs-number", "value": NHS_NUMBER}
    ],
}


@pytest.fixture
def captured_log():
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(
        jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    main.logger.addHandler(handler)
    main.pds_breaker.close()
    try:
        yield buffer
    finally:
        main.logger.removeHandler(handler)
        main.pds_breaker.close()


# 502 and 503 are what pds-client returns for an upstream error and an open
# breaker, so these are the failures transform actually sees.
@pytest.mark.parametrize("status", [502, 503])
def test_a_failed_pds_lookup_does_not_log_the_nhs_number(
    captured_log, monkeypatch, status
) -> None:
    monkeypatch.setattr(main, "PDS_ENABLED", True)

    async def lookup() -> dict:
        async with httpx.AsyncClient() as http:
            with respx.mock:
                respx.get(url__startswith=main.PDS_CLIENT_URL).mock(
                    return_value=httpx.Response(status)
                )
                return await main._enrich_via_pds(http, PATIENT, TRACE_ID)

    result = asyncio.run(lookup())

    assert result == PATIENT, "enrichment should fail open"
    logged = [
        json.loads(line)
        for line in captured_log.getvalue().splitlines()
        if "PDS enrichment failed" in line
    ]
    assert logged, "the failure path did not run"
    assert logged[0]["upstream_status"] == status
    assert logged[0]["trace_id"] == TRACE_ID
    assert NHS_NUMBER not in captured_log.getvalue()
