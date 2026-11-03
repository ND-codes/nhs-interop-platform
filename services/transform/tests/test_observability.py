"""What the pipeline can now see.

Every test here corresponds to something that previously happened in silence:
the message returned 200, the Bundle validated, and no counter moved.
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("DLQ_PATH", tempfile.mkdtemp(prefix="dlq-"))

import httpx  # noqa: E402
import pybreaker  # noqa: E402
import pytest  # noqa: E402
import respx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from prometheus_client import REGISTRY  # noqa: E402

import main  # noqa: E402
from mapper import is_valid_nhs_number  # noqa: E402

FHIR_URL = "http://hapi.test/fhir"

BASE = (
    "MSH|^~\\&|CERNER|GENERAL_HOSP|INTEROP|GENERAL_HOSP|20260101120000||"
    "ADT^A01|MSG00001|P|2.5\r"
    "EVN|A01|20260101120000\r"
    "%s\r"
    "PV1|1|I|WARD1^BED1^ROOM1||||12345^SMITH^JANE|||GEN\r"
)


def sample(value: float | None) -> float:
    return value or 0.0


def post(pid: str, monkeypatch) -> httpx.Response:
    monkeypatch.setattr(main, "PDS_ENABLED", False)
    monkeypatch.setattr(main, "FHIR_BASE_URL", FHIR_URL)
    with respx.mock(assert_all_called=False) as hapi:
        hapi.post(FHIR_URL).mock(
            return_value=httpx.Response(200, json={"resourceType": "Bundle"})
        )
        with TestClient(main.app) as client:
            return client.post(BASE % pid, content=BASE % pid) if False else client.post(
                "/transform", content=BASE % pid, headers={"X-Trace-Id": "obs"}
            )


# --- the alert that could never fire -------------------------------------

def test_breaker_state_is_exported_at_all() -> None:
    """PDSCircuitOpen reads pybreaker_state. Nothing used to export it."""
    value = REGISTRY.get_sample_value("pybreaker_state", {"name": "pds"})
    assert value is not None, "the series the alert watches does not exist"


def test_breaker_state_follows_the_breaker() -> None:
    """The alert fires on == 1, so open has to read as exactly 1."""
    breaker = main.pds_breaker
    breaker.close()
    assert REGISTRY.get_sample_value("pybreaker_state", {"name": "pds"}) == 0

    for _ in range(breaker.fail_max):
        with pytest.raises(Exception):
            breaker.call(_always_fails)

    assert breaker.current_state == pybreaker.STATE_OPEN
    assert REGISTRY.get_sample_value("pybreaker_state", {"name": "pds"}) == 1

    breaker.close()
    assert REGISTRY.get_sample_value("pybreaker_state", {"name": "pds"}) == 0


def _always_fails() -> None:
    raise RuntimeError("PDS is down")


# --- fields that used to vanish ------------------------------------------

def test_an_unparseable_date_is_counted(monkeypatch) -> None:
    labels = {"segment": "PID", "field": "7", "reason": "unparseable_date"}
    before = sample(REGISTRY.get_sample_value("hl7_field_dropped_total", labels))

    response = post(
        "PID|1||9000000009^^^NHS^NH||DOE^JOHN||NOTADATE|M", monkeypatch
    )

    assert response.status_code == 200  # the message still succeeds
    assert sample(
        REGISTRY.get_sample_value("hl7_field_dropped_total", labels)
    ) == before + 1


def test_an_unmapped_address_is_counted(monkeypatch) -> None:
    labels = {"segment": "PID", "field": "11", "reason": "not_mapped"}
    before = sample(REGISTRY.get_sample_value("hl7_field_dropped_total", labels))

    post(
        "PID|1||9000000009^^^NHS^NH||DOE^JOHN||19800101|M|||"
        "1 NHS WAY^^LONDON^^SW1A 1AA^GBR",
        monkeypatch,
    )

    assert sample(
        REGISTRY.get_sample_value("hl7_field_dropped_total", labels)
    ) == before + 1


# --- the identity key -----------------------------------------------------

@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("9000000009", True),
        ("4000000004", True),
        ("1234567890", False),   # check digit would be 10, so invalid
        ("999999999", False),    # nine digits
        ("90000000O9", False),   # letter O for zero
        ("", False),
    ],
)
def test_nhs_number_check_digit(value, valid) -> None:
    """The NHS Data Dictionary calls the Modulus 11 algorithm mandatory."""
    assert is_valid_nhs_number(value) is valid


def test_an_invalid_nhs_number_is_counted_and_not_matched_on(monkeypatch) -> None:
    """A ten digit number that fails its check digit is not an NHS number.

    It is kept on the resource, because the sender sent it, but it must not
    key a conditional update. A Scottish CHI number can arrive in this field
    and will fail this check.
    """
    before = sample(REGISTRY.get_sample_value("nhs_number_invalid_total"))
    before_none = sample(
        REGISTRY.get_sample_value("patient_write_total", {"identity": "none"})
    )

    post("PID|1||1234567890^^^NHS^NH||DOE^JOHN||19800101|M", monkeypatch)

    assert sample(REGISTRY.get_sample_value("nhs_number_invalid_total")) == before + 1
    assert sample(
        REGISTRY.get_sample_value("patient_write_total", {"identity": "none"})
    ) == before_none + 1


def test_a_valid_nhs_number_is_recorded_as_the_identity(monkeypatch) -> None:
    labels = {"identity": "nhs_number"}
    before = sample(REGISTRY.get_sample_value("patient_write_total", labels))

    post("PID|1||9000000009^^^NHS^NH||DOE^JOHN||19800101|M", monkeypatch)

    assert sample(
        REGISTRY.get_sample_value("patient_write_total", labels)
    ) == before + 1
