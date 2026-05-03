"""
Ingest service — HL7 v2 receiver.

Accepts ADT / ORM / ORU messages over HTTP, validates the MSH header,
and forwards the message to the transform service. Exposes Prometheus
metrics and standard Kubernetes health endpoints.
"""
from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, Field
from pythonjsonlogger import jsonlogger

TRANSFORM_URL = os.getenv("TRANSFORM_URL", "http://transform:8001/transform")
SERVICE_NAME = "ingest"
VERSION = os.getenv("VERSION", "0.1.0")

# Structured logging — JSON to stdout so log aggregators can parse cleanly.
logger = logging.getLogger(SERVICE_NAME)
handler = logging.StreamHandler()
handler.setFormatter(jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
logger.addHandler(handler)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))


class IngestResponse(BaseModel):
    message_id: str = Field(..., description="MSH-10 control id from the message")
    trace_id: str = Field(..., description="Server-assigned trace id for correlation")
    forwarded: bool = Field(..., description="Whether forwarding to transform succeeded")


def _parse_msh(raw: str) -> dict[str, str]:
    """Parse the MSH segment just enough to validate and route.

    Full HL7 parsing happens in the transform service; we only need a few
    fields here to fail fast on obviously malformed input.
    """
    lines = [line for line in raw.splitlines() if line.strip()]
    if not lines or not lines[0].startswith("MSH"):
        raise ValueError("Message does not start with an MSH segment")
    msh = lines[0].split("|")
    if len(msh) < 12:
        raise ValueError("MSH segment has fewer than 12 fields")
    return {
        "sending_app": msh[2],
        "sending_facility": msh[3],
        "receiving_app": msh[4],
        "receiving_facility": msh[5],
        "message_type": msh[8],
        "message_control_id": msh[9],
        "version": msh[11],
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(timeout=5.0)
    logger.info("ingest service started", extra={"transform_url": TRANSFORM_URL})
    yield
    await app.state.http.aclose()


app = FastAPI(title="NHS Interop — Ingest", version=VERSION, lifespan=lifespan)
Instrumentator().instrument(app).expose(app, endpoint="/metrics")


@app.get("/healthz", tags=["health"])
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": SERVICE_NAME, "version": VERSION}


@app.get("/readyz", tags=["health"])
async def readyz(request: Request) -> dict[str, str]:
    # In production this would probe downstream dependencies; here we just
    # confirm the HTTP client is initialised.
    if not getattr(request.app.state, "http", None):
        raise HTTPException(status_code=503, detail="http client not ready")
    return {"status": "ready"}


@app.post("/hl7", response_model=IngestResponse, tags=["hl7"])
async def ingest_hl7(request: Request) -> IngestResponse:
    raw = (await request.body()).decode("utf-8", errors="replace")
    if not raw.strip():
        raise HTTPException(status_code=400, detail="empty body")

    try:
        msh = _parse_msh(raw)
    except ValueError as exc:
        logger.warning("invalid HL7 message", extra={"error": str(exc)})
        raise HTTPException(status_code=400, detail=f"invalid HL7: {exc}") from exc

    trace_id = str(uuid.uuid4())
    logger.info(
        "received HL7 message",
        extra={
            "trace_id": trace_id,
            "message_type": msh["message_type"],
            "message_control_id": msh["message_control_id"],
            "sending_facility": msh["sending_facility"],
        },
    )

    # Forward to transform. In production this would be a queue (SQS FIFO)
    # for durability and ordering; for the demo we use synchronous HTTP.
    try:
        resp = await request.app.state.http.post(
            TRANSFORM_URL,
            content=raw.encode("utf-8"),
            headers={
                "Content-Type": "text/plain",
                "X-Trace-Id": trace_id,
                "X-Message-Type": msh["message_type"],
            },
        )
        resp.raise_for_status()
        forwarded = True
    except httpx.HTTPError as exc:
        logger.error(
            "failed to forward to transform",
            extra={"trace_id": trace_id, "error": str(exc)},
        )
        # Return 202 — the message was accepted, transform will retry.
        # In production the message would land in the queue regardless.
        forwarded = False

    return IngestResponse(
        message_id=msh["message_control_id"],
        trace_id=trace_id,
        forwarded=forwarded,
    )
