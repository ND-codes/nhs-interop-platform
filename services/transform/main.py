"""
Transform service — HL7 v2 to FHIR R4.

Receives raw HL7 messages from ingest, transforms them to a FHIR
transaction Bundle, optionally enriches the Patient via PDS, and persists
the bundle to HAPI FHIR. Failures go to a dead-letter path (S3 in prod,
/tmp in the local demo).
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pybreaker
from fastapi import FastAPI, HTTPException, Request
from prometheus_client import Counter, Gauge, Histogram
from prometheus_fastapi_instrumentator import Instrumentator
from pythonjsonlogger import jsonlogger
from tenacity import retry, stop_after_attempt, wait_exponential

from mapper import (
    dropped_fields,
    extract_nhs_number,
    invalid_nhs_numbers,
    map_message,
    parse_message,
)

FHIR_BASE_URL = os.getenv("FHIR_BASE_URL", "http://hapi-fhir:8080/fhir")
PDS_CLIENT_URL = os.getenv("PDS_CLIENT_URL", "http://pds-client:8002/pds")
DLQ_PATH = Path(os.getenv("DLQ_PATH", "/tmp/dlq"))
# A dead-lettered message is the complete raw HL7 message, so the queue holds
# every PID field including the ones the mapper deliberately drops. That makes
# it the most sensitive store in the local stack, and it had no expiry at all.
# 72 hours is long enough to replay a weekend outage on the Monday.
DLQ_RETENTION_HOURS = int(os.getenv("DLQ_RETENTION_HOURS", "72"))
PDS_ENABLED = os.getenv("PDS_ENABLED", "true").lower() == "true"
SERVICE_NAME = "transform"
VERSION = os.getenv("VERSION", "0.1.0")

DLQ_PATH.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger(SERVICE_NAME)
handler = logging.StreamHandler()
handler.setFormatter(jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
logger.addHandler(handler)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

# Circuit breaker protects us from cascading failures when PDS is slow/down.
pds_breaker = pybreaker.CircuitBreaker(fail_max=5, reset_timeout=30, name="pds")

# Custom metrics. The Prometheus instrumentator handles request-level ones.
DLQ_COUNTER = Counter(
    "hl7_dlq_total", "HL7 messages written to the dead-letter path", ["reason"]
)
DLQ_PRUNED = Counter(
    "hl7_dlq_pruned_total",
    "Dead-lettered messages deleted because they passed the retention window",
)
DLQ_OLDEST = Gauge(
    "hl7_dlq_oldest_seconds",
    "Age of the oldest message in the dead-letter path. A retention rule that "
    "nothing measures is not a control, so this is what an auditor can be "
    "shown and what an alert can watch.",
)
PDS_LATENCY = Histogram(
    "pds_lookup_seconds", "Latency of PDS enrichment calls", ["outcome"]
)
FHIR_WRITE = Counter(
    "fhir_write_total", "FHIR bundles written to HAPI FHIR", ["outcome"]
)

# The PDSCircuitOpen alert has watched pybreaker_state since the first week.
# No service has ever exported it, so the rule could not fire: the breaker
# would open, the pipeline would quietly stop enriching, and nothing said so.
# 0 closed, 1 open, 2 half-open, matching the alert's `== 1`.
BREAKER_STATE = Gauge(
    "pybreaker_state", "Circuit breaker state: 0 closed, 1 open, 2 half-open", ["name"]
)
_BREAKER_STATE_VALUES = {
    pybreaker.STATE_CLOSED: 0,
    pybreaker.STATE_OPEN: 1,
    pybreaker.STATE_HALF_OPEN: 2,
}

# A field that fails to map is dropped and the message still succeeds. Until
# now that happened with no counter, no log line and no trace.
FIELD_DROPPED = Counter(
    "hl7_field_dropped_total",
    "Fields present in the message that did not reach the FHIR resource",
    ["segment", "field", "reason"],
)

# How each Patient write was addressed. `nhs_number` means a conditional
# update keyed on the NHS number; `none` means an unconditional create,
# because the message carried no NHS number to key on.
PATIENT_WRITE = Counter(
    "patient_write_total", "Patient writes by the identity used", ["identity"]
)

# An NHS number whose check digit does not validate. The NHS Data Dictionary
# says the Modulus 11 algorithm is mandatory, and we were not running it.
NHS_NUMBER_INVALID = Counter(
    "nhs_number_invalid_total",
    "NHS numbers in PID-3 that failed the Modulus 11 check digit",
)


class _BreakerStateExporter(pybreaker.CircuitBreakerListener):
    """Keep the gauge in step with the breaker.

    pybreaker calls state_change on every transition, which is the only
    moment the value can change, so there is nothing to poll.
    """

    def state_change(self, cb, old_state, new_state) -> None:
        # pybreaker 1.2.0 passes state objects whose `name` is the string
        # 'closed', 'open' or 'half-open'. They carry no `state` attribute,
        # which is worth stating because reading the wrong one fails quietly:
        # the gauge stays at 0 and the alert still never fires.
        BREAKER_STATE.labels(name=cb.name).set(
            _BREAKER_STATE_VALUES.get(getattr(new_state, "name", None), 0)
        )
        logger.warning(
            "circuit breaker state change",
            extra={
                "breaker": cb.name,
                "from": getattr(old_state, "name", None),
                "to": getattr(new_state, "name", None),
            },
        )


pds_breaker.add_listener(_BreakerStateExporter())
# Publish the starting value, so the series exists before the first failure.
BREAKER_STATE.labels(name=pds_breaker.name).set(
    _BREAKER_STATE_VALUES.get(pds_breaker.current_state, 0)
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(timeout=10.0)
    # Prune on startup as well as on write, so a restart clears anything that
    # aged out while the service was down. _prune_dlq is defined below; the
    # name resolves when this runs, not when the module is read.
    _prune_dlq()
    logger.info(
        "transform service started",
        extra={
            "fhir_base_url": FHIR_BASE_URL,
            "pds_enabled": PDS_ENABLED,
            "dlq_retention_hours": DLQ_RETENTION_HOURS,
        },
    )
    yield
    await app.state.http.aclose()


app = FastAPI(title="NHS Interop — Transform", version=VERSION, lifespan=lifespan)
Instrumentator().instrument(app).expose(app, endpoint="/metrics")


@app.get("/healthz", tags=["health"])
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": SERVICE_NAME, "version": VERSION}


@app.get("/readyz", tags=["health"])
def readyz() -> dict[str, str]:
    return {"status": "ready"}


def _record_observations(raw: str, bundle: dict, trace_id: str) -> None:
    """Turn the mapper's own account of what it dropped into metrics.

    The mapper stays free of Prometheus imports: it reports, this translates.
    """
    try:
        parsed = parse_message(raw)
    except Exception:  # noqa: BLE001
        return

    for segment, field, reason in dropped_fields(parsed):
        FIELD_DROPPED.labels(segment=segment, field=field, reason=reason).inc()
        logger.info(
            "field not carried into FHIR",
            extra={
                "trace_id": trace_id,
                "segment": segment,
                "field": field,
                "reason": reason,
            },
        )

    patient = bundle["entry"][0]["resource"]
    for bad in invalid_nhs_numbers(patient):
        NHS_NUMBER_INVALID.inc()
        logger.warning(
            "identifier claims to be an NHS number but fails its check digit, "
            "so it will not be used to match a patient",
            extra={"trace_id": trace_id, "length": len(bad)},
        )

    identity = "nhs_number" if extract_nhs_number(patient) else "none"
    PATIENT_WRITE.labels(identity=identity).inc()


def _dlq_oldest_age() -> float:
    """Age in seconds of the oldest message on disk, read at scrape time.

    This is deliberately not a value set by _prune_dlq. The first version set
    the gauge when pruning ran, and pruning deletes anything past the window,
    so the gauge could never read more than 72 hours and then froze until the
    next write or restart. The alert threshold is 80 hours, so the alert could
    never fire in the one case it existed for: a stack that dead-letters
    nothing and never restarts. Reading the directory on each scrape costs one
    listing of a path that should hold a handful of files.
    """
    oldest_mtime: float | None = None
    for path in DLQ_PATH.glob("*.hl7"):
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            continue
        if oldest_mtime is None or mtime < oldest_mtime:
            oldest_mtime = mtime
    return 0.0 if oldest_mtime is None else time.time() - oldest_mtime


DLQ_OLDEST.set_function(_dlq_oldest_age)


def _prune_dlq() -> int:
    """Delete dead-lettered messages past the retention window.

    Known limit, and it is the reason the production control is an S3
    lifecycle rule rather than this function: pruning runs on write and on
    startup, so a stack that dead-letters nothing and never restarts keeps its
    oldest message past the window. `hl7_dlq_oldest_seconds` is read at
    scrape time, so it keeps rising in that case and DLQMessagesPastRetention
    fires.
    """
    cutoff = time.time() - (DLQ_RETENTION_HOURS * 3600)
    pruned = 0
    for path in DLQ_PATH.glob("*.hl7"):
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            continue
        if mtime < cutoff:
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            pruned += 1
            DLQ_PRUNED.inc()
    if pruned:
        logger.info(
            "pruned dead-lettered messages past retention",
            extra={"pruned": pruned, "retention_hours": DLQ_RETENTION_HOURS},
        )
    return pruned


def _dlq(raw: str, trace_id: str, reason: str) -> None:
    path = DLQ_PATH / f"{trace_id}.hl7"
    path.write_text(raw, encoding="utf-8")
    DLQ_COUNTER.labels(reason=reason).inc()
    logger.warning(
        "message dead-lettered",
        extra={"trace_id": trace_id, "reason": reason, "path": str(path)},
    )
    _prune_dlq()


async def _enrich_via_pds(
    http: httpx.AsyncClient, patient: dict, trace_id: str
) -> dict:
    """Call the PDS client (which wraps the NHS Spine sandbox). On failure
    we fail open — the pipeline continues with locally-derived demographics
    rather than dropping the message."""
    if not PDS_ENABLED:
        return patient
    nhs_number = extract_nhs_number(patient)
    if not nhs_number:
        return patient

    # pybreaker 1.2.0 cannot decorate a coroutine function: the decorator sees
    # the coroutine object returned, counts a success, and the failure raises
    # later on await, outside the breaker. calling() wraps the awaited block.
    async def _call() -> dict | None:
        with pds_breaker.calling(), PDS_LATENCY.labels(outcome="success").time():
            resp = await http.get(
                f"{PDS_CLIENT_URL}/{nhs_number}",
                headers={"X-Trace-Id": trace_id},
                timeout=3.0,
            )
            resp.raise_for_status()
            return resp.json()

    try:
        pds_patient = await _call()
    except (pybreaker.CircuitBreakerError, httpx.HTTPError) as exc:
        PDS_LATENCY.labels(outcome="failure").observe(0)
        # Never str(exc). An HTTPStatusError's message includes the request
        # URL, which is PDS_CLIENT_URL/<nhs_number>, so logging the message
        # logs the patient identifier on every failed lookup.
        status = (
            exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        )
        logger.warning(
            "PDS enrichment failed, continuing with local demographics",
            extra={
                "trace_id": trace_id,
                "error_type": type(exc).__name__,
                "upstream_status": status,
            },
        )
        return patient

    if not pds_patient:
        return patient

    # Merge: trust PDS for name/gender/birthDate, keep local identifiers.
    enriched = {**patient}
    for key in ("name", "gender", "birthDate", "address", "telecom"):
        if pds_patient.get(key):
            enriched[key] = pds_patient[key]
    return enriched


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.2, max=2), reraise=True)
async def _persist_bundle(
    http: httpx.AsyncClient, bundle: dict, trace_id: str
) -> dict:
    resp = await http.post(
        f"{FHIR_BASE_URL}",
        json=bundle,
        headers={
            "Content-Type": "application/fhir+json",
            "X-Trace-Id": trace_id,
        },
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()


@app.post("/transform", tags=["hl7"])
async def transform(request: Request) -> dict:
    trace_id = request.headers.get("X-Trace-Id") or str(uuid.uuid4())
    raw = (await request.body()).decode("utf-8", errors="replace")
    if not raw.strip():
        raise HTTPException(status_code=400, detail="empty body")

    try:
        bundle = map_message(raw, trace_id)
    except Exception as exc:  # noqa: BLE001
        _dlq(raw, trace_id, reason="mapping_error")
        logger.error(
            "mapping failure",
            extra={"trace_id": trace_id, "error": str(exc)},
        )
        raise HTTPException(status_code=422, detail=f"mapping error: {exc}") from exc

    # Count what the message carried and the mapping did not. These used to
    # vanish: the message returns 200, the Bundle validates, and the field is
    # simply absent from the resource.
    _record_observations(raw, bundle, trace_id)

    # Enrich the Patient entry (entry[0]) with PDS data.
    patient_entry = bundle["entry"][0]
    patient_entry["resource"] = await _enrich_via_pds(
        request.app.state.http, patient_entry["resource"], trace_id
    )

    try:
        result = await _persist_bundle(request.app.state.http, bundle, trace_id)
        FHIR_WRITE.labels(outcome="success").inc()
    except httpx.HTTPError as exc:
        _dlq(raw, trace_id, reason="fhir_write_error")
        FHIR_WRITE.labels(outcome="failure").inc()
        logger.error(
            "FHIR write failure",
            extra={"trace_id": trace_id, "error": str(exc)},
        )
        raise HTTPException(status_code=502, detail=f"FHIR write failed: {exc}") from exc

    logger.info(
        "message transformed and persisted",
        extra={
            "trace_id": trace_id,
            "entries": len(bundle["entry"]),
            "bundle_type": "transaction",
        },
    )
    return {
        "trace_id": trace_id,
        "fhir_bundle": result,
        "entries": len(bundle["entry"]),
    }
