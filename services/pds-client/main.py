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
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pybreaker
from cachetools import TTLCache
from fastapi import FastAPI, HTTPException
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


@breaker
async def _fetch_from_sandbox(http: httpx.AsyncClient, nhs_number: str) -> dict:
    url = f"{PDS_BASE_URL}/Patient/{nhs_number}"
    resp = await http.get(url, headers={"X-Request-ID": nhs_number})
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="patient not found in PDS")
    resp.raise_for_status()
    return resp.json()


@app.get("/pds/{nhs_number}", tags=["pds"])
async def lookup(nhs_number: str) -> dict:
    if not _is_valid_nhs_number(nhs_number):
        raise HTTPException(status_code=400, detail="invalid NHS number format")

    if nhs_number in cache:
        logger.info("PDS cache hit", extra={"nhs_number": nhs_number})
        return cache[nhs_number]

    if PDS_STUB:
        logger.info("PDS stub mode — returning canned record", extra={"nhs_number": nhs_number})
        cache[nhs_number] = STUB_RECORD
        return STUB_RECORD

    try:
        record = await _fetch_from_sandbox(app.state.http, nhs_number)
    except pybreaker.CircuitBreakerError:
        logger.warning(
            "PDS circuit open — refusing call",
            extra={"nhs_number": nhs_number},
        )
        raise HTTPException(status_code=503, detail="PDS circuit breaker open")
    except httpx.HTTPError as exc:
        logger.error(
            "PDS upstream error",
            extra={"nhs_number": nhs_number, "error": str(exc)},
        )
        raise HTTPException(status_code=502, detail=f"PDS upstream error: {exc}") from exc

    cache[nhs_number] = record
    return record
