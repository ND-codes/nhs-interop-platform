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

import re
import uuid
from datetime import datetime
from typing import Any

import hl7

NHS_IDENTIFIER_SYSTEM = "https://fhir.nhs.uk/Id/nhs-number"
LOCAL_IDENTIFIER_SYSTEM = "https://fhir.nhs.uk/Id/local-patient-identifier"

# HL7's v2-to-FHIR concept map for table 0001, which rates every one of these
# "is equivalent to". A is ambiguous and N is not applicable; both are `other`
# rather than `unknown`, because the sender knew the answer and it was neither
# male nor female.
_GENDER_MAP = {
    "M": "male",
    "F": "female",
    "O": "other",
    "U": "unknown",
    "A": "other",
    "N": "other",
}
_ENCOUNTER_CLASS_MAP = {
    "I": ("IMP", "inpatient encounter"),
    "O": ("AMB", "ambulatory"),
    "E": ("EMER", "emergency"),
    "P": ("PRENC", "pre-admission"),
}


# HL7 v2.5.1 section 2.7.1: the five escape sequences that stand in for the
# delimiters themselves. Decoded in a single pass so that an escaped escape,
# \E\F\E\, yields the literal text \F\ rather than a field separator.
_ESCAPES = {"F": "|", "S": "^", "T": "&", "R": "~", "E": "\\"}
_ESCAPE_RE = re.compile(r"\\([FSTRE])\\")

# A trailing +HHMM or -HHMM offset on a v2 time stamp.
_TZ_SUFFIX_RE = re.compile(r"[+-]\d{4}$")


def decode_escapes(value: str | None) -> str | None:
    r"""Replace HL7 escape sequences with the characters they stand for.

    Only the five delimiter escapes are handled. Anything else, such as
    \H\ or \Xdd\, is left alone rather than stripped, because dropping
    text we do not understand is worse than passing it through.
    """
    if value is None:
        return None
    return _ESCAPE_RE.sub(lambda m: _ESCAPES[m.group(1)], value)


def _hl7_to_iso(ts: str | None) -> str | None:
    """Convert an HL7 time stamp to ISO 8601, full precision only."""
    if not ts:
        return None
    ts = _TZ_SUFFIX_RE.sub("", str(ts).strip())
    for fmt in ("%Y%m%d%H%M%S", "%Y%m%d%H%M", "%Y%m%d"):
        try:
            return datetime.strptime(ts, fmt).isoformat()
        except ValueError:
            continue
    return None


def hl7_to_fhir_date(ts: str | None) -> str | None:
    """Convert an HL7 time stamp to a FHIR date, keeping its precision.

    FHIR R4 defines `date` as "A date, or partial date (e.g. just year or
    year + month)" in the format "YYYY, YYYY-MM, or YYYY-MM-DD", and says
    "There SHALL be no time zone". A sender that gives us only a year is
    telling us something, so a year is what we keep. The previous version
    recognised YYYYMMDD alone and returned nothing for everything else,
    which turned a partial date of birth into a missing one.
    """
    if not ts:
        return None
    ts = _TZ_SUFFIX_RE.sub("", str(ts).strip())
    digits = ts.split(".")[0]
    for length, fmt, out in (
        (8, "%Y%m%d", "%Y-%m-%d"),
        (6, "%Y%m", "%Y-%m"),
        (4, "%Y", "%Y"),
    ):
        if len(digits) >= length:
            head = digits[:length]
            try:
                return datetime.strptime(head, fmt).strftime(out)
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
        # hl7 library uses 1-based component indexing; callers pass 0-based.
        return str(f[comp + 1]).strip() or None
    except (IndexError, TypeError):
        parts = str(f).split("^")
        return parts[comp].strip() if comp < len(parts) else None


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
        identifiers.append({"system": system, "value": decode_escapes(value)})

    # PID-5 is the person name. Component 1 is the family name; components 2
    # and 3 are the given name and the middle name or initial. FHIR puts both
    # of the latter in `given`, in order, so a middle name is not dropped.
    family = decode_escapes(_field(pid, 5, 0))
    given = [
        decode_escapes(_field(pid, 5, i))
        for i in (1, 2)
        if _field(pid, 5, i)
    ]

    return {
        "resourceType": "Patient",
        "identifier": identifiers,
        "name": [{"use": "official", "family": family, "given": given}],
        "gender": _GENDER_MAP.get(_field(pid, 8) or "U", "unknown"),
        "birthDate": hl7_to_fhir_date(_field(pid, 7)),
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
        observation = {
            "resourceType": "Observation",
            "status": "final",
            "code": {"coding": [{"system": "http://loinc.org", "code": code}]},
            "subject": {"reference": patient_ref},
            "effectiveDateTime": effective,
        }
        # OBX-2 is the value type. It tells us which FHIR value[x] to use.
        observation.update(_observation_value(_field(obx, 2), value, units))
        observations.append(observation)
    return observations


# OBX-2, HL7 v2 table 0125. NM is the only one of these that is a number.
_NUMERIC_VALUE_TYPES = {"NM", "SN"}
_CODED_VALUE_TYPES = {"CE", "CWE", "CF"}


def _observation_value(
    value_type: str | None, value: str, units: str | None
) -> dict[str, Any]:
    """Choose the FHIR value[x] that matches OBX-2.

    The previous version always produced a valueQuantity and, when the text
    would not parse as a number, set that quantity to 0 with the original in
    a made-up `_raw` property. A culture result of POSITIVE became the
    number zero, which is both wrong and the kind of wrong that reads as a
    real measurement downstream.
    """
    value = decode_escapes(value) or value
    vt = (value_type or "").strip().upper()

    if vt in _CODED_VALUE_TYPES:
        return {"valueCodeableConcept": {"text": value}}

    if vt in _NUMERIC_VALUE_TYPES or vt == "":
        try:
            quantity: dict[str, Any] = {"value": float(value)}
        except ValueError:
            # Declared numeric but is not. Keep the text rather than invent
            # a number, and let FHIR validation or a human catch it.
            return {"valueString": value}
        if units:
            quantity["unit"] = decode_escapes(units)
        return {"valueQuantity": quantity}

    return {"valueString": value}


def map_medication_requests(msg: hl7.Message, patient_ref: str) -> list[dict[str, Any]]:
    medication_requests: list[dict[str, Any]] = []
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
            medication_requests.append(
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
    return medication_requests


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


def prune(value: Any) -> Any:
    """Drop empty values so they never reach the JSON.

    FHIR's JSON rules say properties "never have null values", to "Omit a
    property if it is empty", and that "String property values can never be
    empty". An unparseable PID-7 was leaving `"birthDate": null` in the
    resource, and a PID with no name was leaving `"family": ""`.

    Zero and False are kept. A quantity of 0 is a real measurement.
    """
    if isinstance(value, dict):
        pruned = {k: prune(v) for k, v in value.items()}
        return {k: v for k, v in pruned.items() if v not in (None, "", [], {})}
    if isinstance(value, list):
        items = [prune(v) for v in value]
        return [v for v in items if v not in (None, "", [], {})]
    return value


def _urn() -> str:
    """A fresh urn:uuid for a bundle entry.

    FHIR R4 defines `uuid` as "A UUID (aka GUID) represented as a URI", with
    the example urn:uuid:c757873d-ec9a-4326-a141-556f43239520, and says UUIDs
    in URIs are lowercase. The old placeholders, urn:uuid:patient-1 and the
    like, parse as neither. A server is within its rights to reject them.
    """
    return f"urn:uuid:{uuid.uuid4()}"


def map_message(raw: str, trace_id: str) -> dict[str, Any]:
    """Transform a raw HL7 v2 message into a FHIR transaction Bundle."""
    msg = _parse(raw)
    patient = map_patient(msg)

    # HAPI FHIR resolves urn:uuid references inside a transaction Bundle and
    # rewrites them to real resource ids on persist. The value only has to be
    # unique within the bundle, but it does have to be a real UUID.
    patient_fullurl = _urn()
    patient_ref = patient_fullurl

    # Conditional update on the NHS number: PUT [type]?[search parameters].
    # FHIR R4 says that with no matches "The server creates the resource",
    # with one match "The server performs the update against the matching
    # resource", and with several it returns 412.
    #
    # A conditional create would also stop a resend duplicating the patient,
    # but it would ignore an A08 carrying corrected demographics, because one
    # match means do nothing. An update handles both, which is why this is a
    # PUT and not a POST with ifNoneExist.
    patient_request: dict[str, Any] = {"method": "POST", "url": "Patient"}
    nhs_number = extract_nhs_number(patient)
    if nhs_number:
        patient_request = {
            "method": "PUT",
            "url": f"Patient?identifier={NHS_IDENTIFIER_SYSTEM}|{nhs_number}",
        }

    entries: list[dict[str, Any]] = [
        {
            "fullUrl": patient_fullurl,
            "resource": prune(patient),
            "request": patient_request,
        }
    ]

    encounter = map_encounter(msg, patient_ref)
    if encounter:
        entries.append(
            {
                "fullUrl": _urn(),
                "resource": prune(encounter),
                "request": {"method": "POST", "url": "Encounter"},
            }
        )

    for obs in map_observations(msg, patient_ref):
        entries.append(
            {
                "fullUrl": _urn(),
                "resource": prune(obs),
                "request": {"method": "POST", "url": "Observation"},
            }
        )

    for req in map_medication_requests(msg, patient_ref):
        entries.append(
            {
                "fullUrl": _urn(),
                "resource": prune(req),
                "request": {"method": "POST", "url": "MedicationRequest"},
            }
        )

    entries.append(
        {
            "fullUrl": _urn(),
            "resource": prune(map_message_header(msg, trace_id)),
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
