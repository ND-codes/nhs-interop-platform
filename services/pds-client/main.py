"""
PDS client — NHS Spine Personal Demographics Service FHIR sandbox adapter.

Thin, resilient wrapper around the NHS Digital PDS FHIR Sandbox API. It:
  * Authenticates with a sandbox API key (in prod this becomes a signed
    JWT + OAuth 2.0 client-credentials grant via Spine Secure Proxy).
  * Wraps the call in a circuit breaker and bounded cache to protect
    downstream services from PDS flakiness.
  * Returns a minimal FHIR Patient snippet with the fields we care about:
    name, gender, birthDate, address, telecom.

When PDS is configured as the stub (the default for the local demo), the
service returns a deterministic canned record so the pipeline works offline.

Logging rule for this service: the NHS number is a direct patient identifier
and never appears in a log record, a metric label, or an outbound correlation
header. Use the trace id, which transform already sends as X-Trace-Id and
which identifies the message rather than the patient. Note that
python-json-logger serialises everything passed in `extra`, whether or not the
format string names it, so an identifier added to `extra` reaches stdout even
when the log line looks clean.
"""
from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pybreaker
from cachetools import TTLCache
from fastapi import FastAPI, Header, HTTPException
from prometheus_fastapi_instrumentator import Instrumentator
from pythonjsonlogger import jsonlogger

PDS_BASE_URL = os.getenv(
    "PDS_BASE_URL",
    "https://sandbox.api.service.nhs.uk/personal-demographics/FHIR/R4",
)
PDS_API_KEY = os.getenv("PDS_API_KEY", "")
# STUB mode skips the real sandbox call and returns a canned record.
# Useful for local demos and CI (where outbound network may be restricted).
PDS_STUB = os.getenv("PDS_STUB", "true").lower() == "true"
CACHE_TTL = int(os.getenv("PDS_CACHE_TTL_SECONDS", "300"))
CACHE_MAX = int(os.getenv("PDS_CACHE_MAX", "1024"))
SERVICE_NAME = "pds-client"
VERSION = os.getenv("VERSION", "0.1.0")

logger = logging.getLogger(SERVICE_NAME)
handler = logging.StreamHandler()
handler.setFormatter(jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
logger.addHandler(handler)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

breaker = pybreaker.CircuitBreaker(fail_max=5, reset_timeout=30, name="pds-upstream")
cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=CACHE_MAX, ttl=CACHE_TTL)

STUB_RECORD: dict[str, Any] = {
    "resourceType": "Patient",
    "name": [{"use": "official", "family": "Smith", "given": ["Paul"], "prefix": ["Mr"]}],
    "gender": "male",
    "birthDate": "2010-10-22",
    "address": [
        {
            "use": "home",
            "line": ["1 Trevelyan Square", "Boar Lane"],
            "city": "Leeds",
            "district": "West Yorkshire",
            "postalCode": "LS1 6AE",
        }
    ],
    "telecom": [{"system": "phone", "value": "01632960587", "use": "home"}],
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(
        timeout=3.0,
        headers={"apikey": PDS_API_KEY} if PDS_API_KEY else {},
    )
    logger.info(
        "pds-client started",
        extra={"pds_base_url": PDS_BASE_URL, "stub": PDS_STUB},
    )
    yield
    await app.state.http.aclose()


app = FastAPI(title="NHS Interop — PDS Client", version=VERSION, lifespan=lifespan)
Instrumentator().instrument(app).expose(app, endpoint="/metrics")


@app.get("/healthz", tags=["health"])
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": SERVICE_NAME, "version": VERSION}


@app.get("/readyz", tags=["health"])
def readyz() -> dict[str, str]:
    return {"status": "ready"}


def _is_valid_nhs_number(nhs_number: str) -> bool:
    """Basic structural check; full Mod-11 checksum validation omitted here
    for brevity but is required in production."""
    return nhs_number.isdigit() and len(nhs_number) == 10


async def _fetch_from_sandbox(
    http: httpx.AsyncClient, nhs_number: str, trace_id: str
) -> dict:
    """The breaker is entered with `calling()` rather than applied as a
    decorator. pybreaker 1.2.0 cannot decorate a coroutine function: the
    decorator sees the coroutine object returned, counts a success, and the
    failure raises later on await, outside the breaker. `call_async` is not
    the alternative here, because it is implemented with tornado.gen and this
    service runs on asyncio.

    X-Request-ID carries the trace id, not the NHS number. A request id is
    echoed in upstream error responses and recorded by every intermediary, so
    putting a patient identifier in it leaks further than our own log store.
    """
    url = f"{PDS_BASE_URL}/Patient/{nhs_number}"
    with breaker.calling():
        resp = await http.get(url, headers={"X-Request-ID": trace_id})
        if resp.status_code == 404:
            raise HTTPException(status_code=404, detail="patient not found in PDS")
        resp.raise_for_status()
        return resp.json()


@app.get("/pds/{nhs_number}", tags=["pds"])
async def lookup(
    nhs_number: str,
    x_trace_id: str | None = Header(default=None),
) -> dict:
    """transform sends X-Trace-Id on every call. Before this change the header
    was sent and dropped, which left the NHS number as the only identifier
    available to log, which is how it ended up in four log lines."""
    trace_id = x_trace_id or str(uuid.uuid4())

    if not _is_valid_nhs_number(nhs_number):
        raise HTTPException(status_code=400, detail="invalid NHS number format")

    if nhs_number in cache:
        logger.info("PDS cache hit", extra={"trace_id": trace_id})
        return cache[nhs_number]

    if PDS_STUB:
        logger.info(
            "PDS stub mode, returning canned record", extra={"trace_id": trace_id}
        )
        cache[nhs_number] = STUB_RECORD
        return STUB_RECORD

    try:
        record = await _fetch_from_sandbox(app.state.http, nhs_number, trace_id)
    except pybreaker.CircuitBreakerError:
        logger.warning(
            "PDS circuit open, refusing call",
            extra={"trace_id": trace_id},
        )
        raise HTTPException(status_code=503, detail="PDS circuit breaker open")
    except httpx.HTTPError as exc:
        # Never str(exc). httpx builds the message of an HTTPStatusError from
        # the request URL, and the URL ends in /Patient/<nhs_number>, so the
        # message carries the identifier. The type and status code say
        # everything triage needs; the trace id finds the request.
        status = (
            exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        )
        logger.error(
            "PDS upstream error",
            extra={
                "trace_id": trace_id,
                "error_type": type(exc).__name__,
                "upstream_status": status,
            },
        )
        raise HTTPException(
            status_code=502,
            detail=f"PDS upstream error: {type(exc).__name__}"
            + (f" {status}" if status else ""),
        ) from exc

    cache[nhs_number] = record
    return record
