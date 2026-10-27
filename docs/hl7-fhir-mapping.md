# HL7 v2 to FHIR R4 mapping

The transform service implements the minimum mapping needed to demonstrate the
pattern. Real NHS trust mappings use hundreds of HL7 tables. The structure is
the same, just wider.

Every row below was checked against the mapper by running it. Where a field is
not mapped, this document says so rather than describing an intention. An
earlier version of this page claimed three mappings that did not exist.

## Summary

| HL7 v2 segment | FHIR R4 resource | Notes |
|---|---|---|
| MSH | MessageHeader | Source, destination, event type, trace id as a meta tag. |
| PID | Patient | Identifiers, name, sex, date of birth. Enriched from PDS when an NHS number is present. |
| PV1 | Encounter | Class, status, location display. |
| OBX | Observation | Code, value, units, effective time. |
| RXE, RXA | MedicationRequest | Medication code only. |

## Text handling, applied everywhere

HL7 v2.5.1 section 2.7.1 defines five escape sequences so that the delimiters
themselves can appear inside a field. They are decoded before any value
reaches FHIR.

| Escape | Character | Example in, text out |
|---|---|---|
| `\F\` | `|` field separator | `SMITH\F\JONES` becomes `SMITH\|JONES` |
| `\S\` | `^` component separator | `A\S\B` becomes `A^B` |
| `\T\` | `&` subcomponent separator | `O\T\BRIEN` becomes `O&BRIEN` |
| `\R\` | `~` repetition separator | `ST\R\JOHN` becomes `ST~JOHN` |
| `\E\` | `\` escape character | `D\E\ANGELO` becomes `D\ANGELO` |

Decoding is a single pass, so `\E\F\E\` yields the literal text `\F\` rather
than a field separator. Sequences outside this set, such as `\H\` for start of
highlighting or `\Xdd\` for hexadecimal data, are passed through unchanged.
Dropping text we do not understand is worse than carrying it.

## Dates

FHIR R4 defines `date` as "A date, or partial date (e.g. just year or year +
month)" with the format "YYYY, YYYY-MM, or YYYY-MM-DD", and says "There SHALL
be no time zone".

A v2 time stamp carries as much precision as the sender had. The mapper keeps
that precision instead of requiring all eight digits.

| PID-7 | Patient.birthDate |
|---|---|
| `19800101` | `1980-01-01` |
| `198001` | `1980-01` |
| `1980` | `1980` |
| `19800101120000` | `1980-01-01` |
| `19800101120000+0100` | `1980-01-01` |
| anything unparseable | absent |

A trailing `+HHMM` or `-HHMM` offset is stripped, because a FHIR date carries
no time zone.

## PID to Patient

| HL7 field | FHIR target | Notes |
|---|---|---|
| PID-3 (CX, repeating) | `Patient.identifier` | Assigning authority `NHS` maps to the NHS number system. Anything else maps to the local patient identifier system. |
| PID-5.1 | `Patient.name[0].family` | Escape sequences decoded. |
| PID-5.2 | `Patient.name[0].given[0]` | Given name. |
| PID-5.3 | `Patient.name[0].given[1]` | Middle name or initial. FHIR holds both given names in order. |
| PID-7 | `Patient.birthDate` | See Dates above. |
| PID-8 | `Patient.gender` | Per HL7's own concept map, below. |
| PID-11 | not mapped | The address is in the message and is not carried into the Patient resource. It arrives only through PDS enrichment. |
| PID-13 | not mapped | Likewise the telephone number. |

### PID-8, following HL7's concept map

HL7 publishes a concept map from v2 table 0001 to FHIR's
`administrative-gender` in the v2-to-FHIR implementation guide. Every row is
rated "is equivalent to".

| PID-8 | Patient.gender |
|---|---|
| `M` | `male` |
| `F` | `female` |
| `O` | `other` |
| `U` | `unknown` |
| `A` (ambiguous) | `other` |
| `N` (not applicable) | `other` |

Anything outside the table becomes `unknown`. An earlier version of this page
treated `A` and `N` as having no clean target and sent both to `unknown`,
which records a known answer as a missing one.

### Open question: PID-3 beyond CX-1 and CX-4

Only the identifier value (CX-1) and the assigning authority (CX-4) are read.
The CX-5 identifier type code, `NH` or `MR`, is dropped, no `use` is set, and
every non-NHS identifier lands in a single `local-patient-identifier` system
with no assigner. Medical record numbers issued by two different trusts
therefore collide.

The [North West GMSA HL7 v2 guide](https://nw-gmsa.github.io/en/hl7v2.html#pid)
treats CX-1, CX-4 and CX-5 as mandatory and uses the ODS code as the assigning
authority for an MRN. The repo's fixture uses `GENERAL_HOSP`, which is not an
ODS code, so fixing this properly needs the fixtures changed as well.

## PV1 to Encounter

| HL7 field | FHIR target | Notes |
|---|---|---|
| PV1-2 | `Encounter.class` | `I` to IMP, `O` to AMB, `E` to EMER, `P` to PRENC. Anything else falls back to AMB. |
| PV1-3 | `Encounter.location[0].location.display` | Ward, bed and room as a single display string, not split into components. |
| PV1-19 | not mapped | The visit number. See below. |
| PV1-44 | not mapped | The admit time, so the Encounter carries no `period`. |

### Open question: Encounter identity

The Encounter has no `identifier` and no `period`, so there is nothing to
match on. Every message that carries a PV1 creates another Encounter, and a
resent admission produces a duplicate.

PV1-19, the visit number, is the natural key, and that is the direction this
is heading. It is not mapped yet because the visit number is not guaranteed to
be present, and a conditional create whose condition is empty is worse than no
condition at all: it would match every Encounter without a visit number and
merge them. The absence case needs deciding before the mapping is added.

## OBX to Observation

OBX-2 is the value type, and it decides which FHIR `value[x]` is used.

| OBX-2 | FHIR target | Notes |
|---|---|---|
| `NM`, `SN` | `Observation.valueQuantity` | Units from OBX-6.1. |
| `CE`, `CWE`, `CF` | `Observation.valueCodeableConcept` | Carried as `text`; no code system is resolved. |
| anything else | `Observation.valueString` | |
| declared `NM` but not a number | `Observation.valueString` | The text is kept rather than a number invented. |

| HL7 field | FHIR target |
|---|---|
| OBX-3.1 | `Observation.code.coding[0].code`, with a LOINC system |
| OBX-5 | the `value[x]` chosen above |
| OBX-6.1 | `Observation.valueQuantity.unit` |
| OBX-14 | `Observation.effectiveDateTime` |

An earlier version of the mapper always produced a `valueQuantity`, and set
that quantity to `0` with the original text in a `_raw` property when the
value would not parse as a number. A culture result of `POSITIVE` became the
number zero. `_raw` is not a FHIR element; it was invented. Both are gone.

## RXE and RXA to MedicationRequest

| HL7 field | FHIR target | Notes |
|---|---|---|
| RXE-2.1, RXA-5.1 | `MedicationRequest.medicationCodeableConcept.coding[0].code` | SNOMED system asserted without checking. |
| RXE-2.2, RXA-5.2 | the same coding's `display` | |
| RXE-4 | not mapped | No `dosageInstruction` is produced. |
| RXE-13 | not mapped | No `requester` is produced. |

Status is hard-coded to `active` and intent to `order`, for every message.

## PDS enrichment

When PID-3 carries an `NHS` identifier, transform calls `pds-client` before
persisting. PDS values overlay `name`, `gender`, `birthDate`, `address` and
`telecom`. Local identifiers are always kept.

If PDS is unreachable, or the circuit breaker is open, the pipeline fails open
and continues with the demographics from the message. In the local demo the
stub answers for every NHS number, so the stored patient takes the stub's name
rather than the one in the message.

## Bundle shape

Every resource from one message goes into a single FHIR `transaction` Bundle,
so the admission is stored whole or not at all.

Each entry has a `fullUrl` of the form `urn:uuid:<uuid>`, using a real RFC 4122
UUID. FHIR R4 defines `uuid` as "A UUID (aka GUID) represented as a URI" and
requires lowercase. Earlier versions used placeholders such as
`urn:uuid:patient-1`, which are not UUIDs; a server is entitled to reject them.
HAPI FHIR resolves these references and rewrites them to real resource ids on
persist.

The Patient entry is a conditional update when an NHS number is present:

```json
{
  "method": "PUT",
  "url": "Patient?identifier=https://fhir.nhs.uk/Id/nhs-number|9000000009"
}
```

FHIR R4 gives conditional update the form `PUT [base]/[type]?[search
parameters]`. With no matches "The server creates the resource", with one
match "The server performs the update against the matching resource", and
with several it returns 412.

A conditional create, using `ifNoneExist`, would also stop a resend
duplicating the patient. It is the wrong tool here because an A08 is a patient
update: one match means do nothing, so a corrected date of birth would be
discarded along with the duplicate. The update does both jobs.

When there is no NHS number the entry stays an unconditional `POST`. A
condition that cannot be expressed is worse than none, because it would match
every patient that also lacks an NHS number and merge them.

### Known gap: replay ordering

Now that writes update rather than create, replaying an older message from the
dead-letter folder after a newer one has been applied will overwrite newer
data with older. Checking MSH-7 against what was last applied, at the point of
the write, is the fix. Using the NHS number as an SQS FIFO message group id
orders live traffic but does nothing for a replay.

## Empty values

Nothing empty reaches the JSON. FHIR's JSON rules say properties "never have
null values", to "Omit a property if it is empty", and that "String property
values can never be empty".

Each resource is pruned before it goes into the Bundle: `None`, empty strings,
empty lists and empty objects are removed. An unparseable PID-7 used to leave
`"birthDate": null`, and a PID with no name left `"family": ""`.

`0` and `false` survive pruning. A quantity of zero is a real measurement.

## Known gap: silent field failures

Only a whole-message parse failure dead-letters. A single field that fails to
map produces no flag, no metric and no log line. Every defect listed on this
page survived that way.
