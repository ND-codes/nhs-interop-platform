"""The places HL7 v2 to FHIR mapping goes quietly wrong.

Each test here failed against the mapper as it stood before this change.
None of them raised an error at the time: every one produced a Bundle that
a FHIR server would accept while holding the wrong thing, which is why they
are tests rather than exception handling.
"""
from __future__ import annotations

import json
import uuid

import pytest

from mapper import (
    NHS_IDENTIFIER_SYSTEM,
    decode_escapes,
    hl7_to_fhir_date,
    map_message,
    prune,
)

MSH = (
    "MSH|^~\\&|CERNER|GENERAL_HOSP|INTEROP|GENERAL_HOSP|20260101120000||"
    "ADT^A01|MSG00001|P|2.5"
)
EVN = "EVN|A01|20260101120000"
PV1 = "PV1|1|I|WARD1^BED1^ROOM1||||12345^SMITH^JANE|||GEN"


def build(pid: str) -> str:
    """An ADT^A01 with the given PID segment, terminated the way v2 says."""
    return "\r".join([MSH, EVN, pid, PV1]) + "\r"


def patient_in(bundle: dict) -> dict:
    return next(
        e["resource"]
        for e in bundle["entry"]
        if e["resource"]["resourceType"] == "Patient"
    )


def patient_entry(bundle: dict) -> dict:
    return next(
        e for e in bundle["entry"] if e["resource"]["resourceType"] == "Patient"
    )


# --- the middle name ------------------------------------------------------

def test_middle_name_is_kept() -> None:
    """PID-5 component 3 is a given name too, so FHIR keeps both in order."""
    bundle = map_message(
        build("PID|1||9000000009^^^NHS^NH||DOE^JOHN^A||19800101|M"), "t"
    )
    assert patient_in(bundle)["name"][0]["given"] == ["JOHN", "A"]


# --- date precision -------------------------------------------------------

@pytest.mark.parametrize(
    ("stamp", "expected"),
    [
        ("19800101", "1980-01-01"),
        ("198001", "1980-01"),
        ("1980", "1980"),
        ("19800101120000", "1980-01-01"),
        ("19800101120000+0100", "1980-01-01"),
        ("19800101-0500", "1980-01-01"),
        ("", None),
        ("notadate", None),
    ],
)
def test_birth_date_keeps_the_precision_it_was_given(stamp, expected) -> None:
    """FHIR allows YYYY and YYYY-MM. Dropping them loses the date entirely."""
    assert hl7_to_fhir_date(stamp) == expected


def test_partial_birth_date_survives_the_full_mapping() -> None:
    bundle = map_message(
        build("PID|1||9000000009^^^NHS^NH||DOE^JOHN||198001|M"), "t"
    )
    assert patient_in(bundle)["birthDate"] == "1980-01"


# --- escape sequences -----------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "decoded"),
    [
        ("O\\T\\BRIEN", "O&BRIEN"),
        ("SMITH\\F\\JONES", "SMITH|JONES"),
        ("ST\\R\\JOHN", "ST~JOHN"),
        ("A\\S\\B", "A^B"),
        ("D\\E\\ANGELO", "D\\ANGELO"),
        # An escaped escape must not re-trigger decoding of what follows.
        ("\\E\\F\\E\\", "\\F\\"),
        # Sequences we do not handle are left alone, not stripped.
        ("X\\H\\Y", "X\\H\\Y"),
        ("plain", "plain"),
    ],
)
def test_escape_sequences_are_decoded(raw, decoded) -> None:
    assert decode_escapes(raw) == decoded


def test_escaped_name_reaches_the_patient_decoded() -> None:
    bundle = map_message(
        build("PID|1||9000000009^^^NHS^NH||O\\T\\BRIEN^JOHN||19800101|M"), "t"
    )
    assert patient_in(bundle)["name"][0]["family"] == "O&BRIEN"


# --- bundle identity ------------------------------------------------------

def test_full_urls_are_real_uuids_and_unique() -> None:
    """FHIR's uuid type is an RFC 4122 UUID. urn:uuid:patient-1 is not one."""
    bundle = map_message(
        build("PID|1||9000000009^^^NHS^NH||DOE^JOHN||19800101|M"), "t"
    )
    full_urls = [e["fullUrl"] for e in bundle["entry"]]

    for full_url in full_urls:
        assert full_url.startswith("urn:uuid:")
        parsed = uuid.UUID(full_url.removeprefix("urn:uuid:"))
        assert str(parsed) == full_url.removeprefix("urn:uuid:")  # lowercase

    assert len(set(full_urls)) == len(full_urls)


def test_patient_is_a_conditional_update_on_the_nhs_number() -> None:
    """A plain POST made a new Patient for every message. A conditional
    update instead creates when absent and updates when present, so a resend
    does not duplicate and an A08 with corrected demographics still lands."""
    bundle = map_message(
        build("PID|1||9000000009^^^NHS^NH||DOE^JOHN||19800101|M"), "t"
    )
    request = patient_entry(bundle)["request"]
    assert request["method"] == "PUT"
    assert request["url"] == (
        f"Patient?identifier={NHS_IDENTIFIER_SYSTEM}|9000000009"
    )


def test_no_nhs_number_means_an_unconditional_create() -> None:
    """A condition we cannot express is worse than none: it would match
    every patient without an NHS number and silently merge them."""
    bundle = map_message(
        build("PID|1||L12345^^^GENERAL_HOSP^MR||DOE^JOHN||19800101|M"), "t"
    )
    request = patient_entry(bundle)["request"]
    assert request == {"method": "POST", "url": "Patient"}


# --- nothing empty reaches the JSON ---------------------------------------

def test_unparseable_birth_date_is_omitted_not_null() -> None:
    """FHIR JSON says properties "never have null values"."""
    bundle = map_message(
        build("PID|1||9000000009^^^NHS^NH||DOE^JOHN||notadate|M"), "t"
    )
    patient = patient_in(bundle)
    assert "birthDate" not in patient
    assert "null" not in json.dumps(patient)


def test_absent_name_does_not_become_an_empty_string() -> None:
    """FHIR JSON says "String property values can never be empty"."""
    bundle = map_message(build("PID|1||9000000009^^^NHS^NH||||19800101|M"), "t")
    patient = patient_in(bundle)
    assert '""' not in json.dumps(patient)
    assert "[]" not in json.dumps(patient)


def test_pruning_keeps_a_measurement_of_zero() -> None:
    """Zero is a real reading. Only genuinely empty values are dropped."""
    assert prune({"value": 0, "unit": "", "note": None, "ok": False}) == {
        "value": 0,
        "ok": False,
    }


# --- administrative sex ---------------------------------------------------

@pytest.mark.parametrize(
    ("code", "expected"),
    [("M", "male"), ("F", "female"), ("O", "other"), ("U", "unknown"),
     ("A", "other"), ("N", "other"), ("", "unknown"), ("Z", "unknown")],
)
def test_administrative_sex_follows_the_hl7_concept_map(code, expected) -> None:
    """HL7's v2-to-FHIR map for table 0001 rates A and N "is equivalent to"
    other, not unknown: the sender knew, and the answer was neither male nor
    female."""
    bundle = map_message(
        build("PID|1||9000000009^^^NHS^NH||DOE^JOHN||19800101|%s" % code), "t"
    )
    assert patient_in(bundle)["gender"] == expected


# --- observation values ---------------------------------------------------

ORU = (
    "MSH|^~\\&|LAB|GENERAL_HOSP|INTEROP|GENERAL_HOSP|20260101140000||"
    "ORU^R01|MSG00002|P|2.5\r"
    "PID|1||9000000009^^^NHS^NH||DOE^JOHN||19800101|M\r"
    "OBX|1|NM|8867-4^Heart rate^LN||72|bpm||||F|||20260101140000\r"
    "OBX|2|ST|625-4^Culture^LN||POSITIVE|||||F|||20260101140000\r"
    "OBX|3|CE|664-3^Appearance^LN||CLEAR|||||F|||20260101140000\r"
    "OBX|4|NM|999-9^Broken^LN||NOTANUMBER|mg||||F|||20260101140000\r"
)


def observations() -> list[dict]:
    bundle = map_message(ORU, "t")
    return [
        e["resource"]
        for e in bundle["entry"]
        if e["resource"]["resourceType"] == "Observation"
    ]


def test_numeric_observation_is_a_quantity() -> None:
    heart_rate = observations()[0]
    assert heart_rate["valueQuantity"] == {"value": 72.0, "unit": "bpm"}


def test_text_observation_is_not_turned_into_a_number() -> None:
    """A culture result of POSITIVE used to become valueQuantity 0."""
    culture = observations()[1]
    assert "valueQuantity" not in culture
    assert culture["valueString"] == "POSITIVE"


def test_coded_observation_uses_a_codeable_concept() -> None:
    appearance = observations()[2]
    assert appearance["valueCodeableConcept"]["text"] == "CLEAR"
    assert "valueQuantity" not in appearance


def test_numeric_type_with_unparseable_text_keeps_the_text() -> None:
    """Declared NM but is not a number. Keep what was sent, invent nothing."""
    broken = observations()[3]
    assert "valueQuantity" not in broken
    assert broken["valueString"] == "NOTANUMBER"


def test_no_observation_carries_the_invented_raw_property() -> None:
    for observation in observations():
        assert "_raw" not in observation
        assert "_raw" not in observation.get("valueQuantity", {})
