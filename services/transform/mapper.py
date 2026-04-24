"""
HL7 v2 to FHIR R4 mapper.

Implements the minimum mapping needed to demonstrate the pattern:
  PID  → Patient
  PV1  → Encounter
  OBX  → Observation
  RXE / RXA → MedicationRequest
  MSH  → MessageHeader

Mappings are deliberately small and explicit. Real NHS trust mappings use
hundreds of HL7 tables; the structure here is the same, just wider.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import hl7

NHS_IDENTIFIER_SYSTEM = "https://fhir.nhs.uk/Id/nhs-number"
LOCAL_IDENTIFIER_SYSTEM = "https://fhir.nhs.uk/Id/local-patient-identifier"

_GENDER_MAP = {"M": "male", "F": "female", "O": "other", "U": "unknown"}
_ENCOUNTER_CLASS_MAP = {
    "I": ("IMP", "inpatient encounter"),
    "O": ("AMB", "ambulatory"),
    "E": ("EMER", "emergency"),
    "P": ("PRENC", "pre-admission"),
}


def _hl7_to_iso(ts: str | None) -> str | None:
    """Convert HL7 YYYYMMDDHHMMSS timestamp to ISO 8601."""
    if not ts:
        return None
    ts = str(ts).strip()
    for fmt in ("%Y%m%d%H%M%S", "%Y%m%d%H%M", "%Y%m%d"):
        try:
            return datetime.strptime(ts, fmt).isoformat()
        except ValueError:
            continue
    return None


def _field(seg, idx: int, comp: int | None = None) -> str | None:
    """Safely pull a field (and optional component) from an HL7 segment."""
    try:
        f = seg[idx]
    except (IndexError, KeyError):
        return None
    if f is None:
        return None
    if comp is None:
        return str(f).strip() or None
    try:
        return str(f[comp]).strip() or None
    except (IndexError, TypeError):
        return str(f).strip() or None


def _parse(message: str) -> hl7.Message:
    # HL7 v2 uses \r as segment terminator; normalise both \r\n and \n.
    normalised = message.replace("\r\n", "\r").replace("\n", "\r").strip()
    return hl7.parse(normalised)


def map_patient(msg: hl7.Message) -> dict[str, Any]:
    pid = msg.segment("PID")
    identifiers: list[dict[str, Any]] = []

    # PID-3 may contain multiple repeating identifiers, each with components.
    cx = pid[3]
    for rep in cx if hasattr(cx, "__iter__") else [cx]:
        try:
            value = str(rep[0]).strip() if rep[0] else None
            system_code = str(rep[3]).strip() if len(rep) > 3 and rep[3] else None
        except (IndexError, TypeError):
            continue
        if not value:
            continue
        system = NHS_IDENTIFIER_SYSTEM if system_code == "NHS" else LOCAL_IDENTIFIER_SYSTEM
        identifiers.append({"system": system, "value": value})

    # PID-5 is the person name, with surname and given name as components.
    family = _field(pid, 5, 0)
    given = _field(pid, 5, 1)

    return {
        "resourceType": "Patient",
        "identifier": identifiers,
        "name": [{"use": "official", "family": family, "given": [given] if given else []}],
        "gender": _GENDER_MAP.get(_field(pid, 8) or "U", "unknown"),
        "birthDate": (_hl7_to_iso(_field(pid, 7)) or "").split("T")[0] or None,
    }


def map_encounter(msg: hl7.Message, patient_ref: str) -> dict[str, Any] | None:
    try:
        pv1 = msg.segment("PV1")
    except KeyError:
        return None

    cls_code, cls_display = _ENCOUNTER_CLASS_MAP.get(
        _field(pv1, 2) or "O", ("AMB", "ambulatory")
    )
    return {
        "resourceType": "Encounter",
        "status": "in-progress",
        "class": {
            "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
            "code": cls_code,
            "display": cls_display,
        },
        "subject": {"reference": patient_ref},
        "location": [{"location": {"display": _field(pv1, 3) or "unknown"}}],
    }


def map_observations(msg: hl7.Message, patient_ref: str) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    try:
        obx_segments = msg.segments("OBX")
    except KeyError:
        return observations

    for obx in obx_segments:
        code = _field(obx, 3, 0)
        value = _field(obx, 5)
        units = _field(obx, 6, 0)
        effective = _hl7_to_iso(_field(obx, 14))
        if not code or value is None:
            continue
        observations.append(
            {
                "resourceType": "Observation",
                "status": "final",
                "code": {"coding": [{"system": "http://loinc.org", "code": code}]},
                "subject": {"reference": patient_ref},
                "effectiveDateTime": effective,
                "valueQuantity": _value_quantity(value, units),
            }
        )
    return observations


def _value_quantity(value: str, units: str | None) -> dict[str, Any]:
    try:
        num = float(value)
        q: dict[str, Any] = {"value": num}
        if units:
            q["unit"] = units
        return q
    except ValueError:
        return {"value": 0, "unit": units or "", "comparator": None, "_raw": value}


def map_medication_requests(msg: hl7.Message, patient_ref: str) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for seg_name in ("RXE", "RXA"):
        try:
            segments = msg.segments(seg_name)
        except KeyError:
            continue
        for seg in segments:
            # RXE-2 / RXA-5: Give code
            code = _field(seg, 2, 0) if seg_name == "RXE" else _field(seg, 5, 0)
            display = _field(seg, 2, 1) if seg_name == "RXE" else _field(seg, 5, 1)
            if not code:
                continue
            requests.append(
                {
                    "resourceType": "MedicationRequest",
                    "status": "active",
                    "intent": "order",
                    "medicationCodeableConcept": {
                        "coding": [
                            {
                                "system": "http://snomed.info/sct",
                                "code": code,
                                "display": display,
                            }
                        ]
                    },
                    "subject": {"reference": patient_ref},
                }
            )
    return requests


def map_message_header(msg: hl7.Message, trace_id: str) -> dict[str, Any]:
    msh = msg.segment("MSH")
    return {
        "resourceType": "MessageHeader",
        "eventUri": f"urn:hl7:{_field(msh, 9) or 'unknown'}",
        "source": {
            "name": _field(msh, 3),
            "software": _field(msh, 2),
            "endpoint": f"urn:facility:{_field(msh, 4) or 'unknown'}",
        },
        "destination": [
            {
                "name": _field(msh, 5),
                "endpoint": f"urn:facility:{_field(msh, 6) or 'unknown'}",
            }
        ],
        "meta": {
            "tag": [
                {
                    "system": "https://nhs-interop.example/trace",
                    "code": trace_id,
                }
            ]
        },
    }


def map_message(raw: str, trace_id: str) -> dict[str, Any]:
    """Transform a raw HL7 v2 message into a FHIR transaction Bundle."""
    msg = _parse(raw)
    patient = map_patient(msg)
    # Patient URN placeholder — HAPI FHIR resolves urn:uuid references in a
    # transaction Bundle and rewrites them to real resource ids on persist.
    patient_fullurl = "urn:uuid:patient-1"
    patient_ref = patient_fullurl

    entries: list[dict[str, Any]] = [
        {
            "fullUrl": patient_fullurl,
            "resource": patient,
            "request": {"method": "POST", "url": "Patient"},
        }
    ]

    encounter = map_encounter(msg, patient_ref)
    if encounter:
        entries.append(
            {
                "fullUrl": "urn:uuid:encounter-1",
                "resource": encounter,
                "request": {"method": "POST", "url": "Encounter"},
            }
        )

    for i, obs in enumerate(map_observations(msg, patient_ref), start=1):
        entries.append(
            {
                "fullUrl": f"urn:uuid:observation-{i}",
                "resource": obs,
                "request": {"method": "POST", "url": "Observation"},
            }
        )

    for i, req in enumerate(map_medication_requests(msg, patient_ref), start=1):
        entries.append(
            {
                "fullUrl": f"urn:uuid:medreq-{i}",
                "resource": req,
                "request": {"method": "POST", "url": "MedicationRequest"},
            }
        )

    entries.append(
        {
            "fullUrl": "urn:uuid:messageheader-1",
            "resource": map_message_header(msg, trace_id),
            "request": {"method": "POST", "url": "MessageHeader"},
        }
    )

    return {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": entries,
    }


def extract_nhs_number(patient: dict[str, Any]) -> str | None:
    """Return the NHS number from a Patient resource if present."""
    for ident in patient.get("identifier", []) or []:
        if ident.get("system") == NHS_IDENTIFIER_SYSTEM and ident.get("value"):
            return ident["value"]
    return None
