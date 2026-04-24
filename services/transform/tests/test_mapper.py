"""Unit tests for the HL7 → FHIR mapper."""
from __future__ import annotations

from mapper import extract_nhs_number, map_message, NHS_IDENTIFIER_SYSTEM

ADT_WITH_NHS = (
    "MSH|^~\\&|CERNER|GENERAL_HOSP|INTEROP|GENERAL_HOSP|20260101120000||"
    "ADT^A01|MSG00001|P|2.5\r"
    "EVN|A01|20260101120000\r"
    "PID|1||9000000009^^^NHS^NH~L12345^^^GENERAL_HOSP^MR||DOE^JOHN^A||"
    "19800101|M|||1 NHS WAY^^LONDON^^SW1A 1AA^GBR\r"
    "PV1|1|I|WARD1^BED1^ROOM1||||12345^SMITH^JANE|||GEN\r"
)

ORU_WITH_OBX = (
    "MSH|^~\\&|LAB|GENERAL_HOSP|INTEROP|GENERAL_HOSP|20260101140000||"
    "ORU^R01|MSG00002|P|2.5\r"
    "PID|1||9000000009^^^NHS^NH||DOE^JOHN^A||19800101|M\r"
    "OBX|1|NM|8867-4^Heart rate^LN||72|bpm||||F|||20260101140000\r"
    "OBX|2|NM|8310-5^Body temperature^LN||37.1|Cel||||F|||20260101140000\r"
)


def test_maps_patient_and_encounter() -> None:
    bundle = map_message(ADT_WITH_NHS, trace_id="t-1")
    assert bundle["resourceType"] == "Bundle"
    assert bundle["type"] == "transaction"

    resource_types = [e["resource"]["resourceType"] for e in bundle["entry"]]
    assert "Patient" in resource_types
    assert "Encounter" in resource_types
    assert "MessageHeader" in resource_types

    patient = next(e["resource"] for e in bundle["entry"]
                   if e["resource"]["resourceType"] == "Patient")
    assert patient["gender"] == "male"
    assert patient["birthDate"] == "1980-01-01"
    assert any(
        i["system"] == NHS_IDENTIFIER_SYSTEM and i["value"] == "9000000009"
        for i in patient["identifier"]
    )
    assert patient["name"][0]["family"] == "DOE"
    assert patient["name"][0]["given"] == ["JOHN"]


def test_extracts_nhs_number() -> None:
    bundle = map_message(ADT_WITH_NHS, trace_id="t-2")
    patient = bundle["entry"][0]["resource"]
    assert extract_nhs_number(patient) == "9000000009"


def test_maps_observations() -> None:
    bundle = map_message(ORU_WITH_OBX, trace_id="t-3")
    observations = [
        e["resource"]
        for e in bundle["entry"]
        if e["resource"]["resourceType"] == "Observation"
    ]
    assert len(observations) == 2
    heart_rate = observations[0]
    assert heart_rate["code"]["coding"][0]["code"] == "8867-4"
    assert heart_rate["valueQuantity"]["value"] == 72.0
    assert heart_rate["valueQuantity"]["unit"] == "bpm"


def test_handles_missing_pv1() -> None:
    msg_no_pv1 = ADT_WITH_NHS.rsplit("\rPV1", 1)[0] + "\r"
    bundle = map_message(msg_no_pv1, trace_id="t-4")
    resource_types = [e["resource"]["resourceType"] for e in bundle["entry"]]
    assert "Patient" in resource_types
    assert "Encounter" not in resource_types
