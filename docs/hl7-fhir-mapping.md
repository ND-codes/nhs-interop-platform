# HL7 v2 to FHIR R4 mapping

The transform service implements the minimum mapping needed to demonstrate the pattern. Real NHS trust mappings use hundreds of HL7 tables, but the structure is the same — just wider.

## Summary

| HL7 v2 segment | FHIR R4 resource    | Notes                                                                |
|----------------|---------------------|----------------------------------------------------------------------|
| MSH            | MessageHeader       | Source, destination, timestamp, trace id.                            |
| PID            | Patient             | Identifiers, name, gender, DOB. Enriched via PDS if NHS number present. |
| PV1            | Encounter           | Class, status, service provider, hospitalisation.                    |
| OBX            | Observation         | Code, value, units, effective time. Used for labs / vitals.          |
| RXE, RXA       | MedicationRequest   | Medication, dosage, requester.                                       |

## PID → Patient

| HL7 field       | FHIR target                        | Notes                                                       |
|-----------------|------------------------------------|-------------------------------------------------------------|
| PID-3 (CX, rep) | Patient.identifier                 | If assigning authority = `NHS`, system = NHS number URI. Otherwise the local trust identifier system. |
| PID-5.1         | Patient.name.family                | Surname.                                                    |
| PID-5.2         | Patient.name.given[0]              | Given name.                                                 |
| PID-7           | Patient.birthDate                  | HL7 YYYYMMDD → ISO 8601 date.                               |
| PID-8           | Patient.gender                     | `M`/`F`/`O`/`U` → `male`/`female`/`other`/`unknown`.        |
| PID-11          | Patient.address                    | Street / city / postal code. Left as local in the demo.     |

## PV1 → Encounter

| HL7 field | FHIR target          | Notes                                                     |
|-----------|----------------------|-----------------------------------------------------------|
| PV1-2     | Encounter.class      | `I` → IMP (inpatient), `O` → AMB, `E` → EMER, `P` → PRENC. |
| PV1-3     | Encounter.location   | Ward / bed / room concatenated into display text.         |
| PV1-44    | Encounter.period.start | Admit time (not mapped in the demo, but trivially added). |

## OBX → Observation

| HL7 field | FHIR target                          |
|-----------|--------------------------------------|
| OBX-3.1   | Observation.code.coding[0].code      |
| OBX-5     | Observation.valueQuantity.value      |
| OBX-6.1   | Observation.valueQuantity.unit       |
| OBX-14    | Observation.effectiveDateTime        |

Non-numeric OBX values are preserved in an `_raw` extension so the downstream can still recover intent.

## RXE / RXA → MedicationRequest

| HL7 field         | FHIR target                                             |
|-------------------|---------------------------------------------------------|
| RXE-2 / RXA-5     | MedicationRequest.medicationCodeableConcept.coding      |
| RXE-4  / RXA-6    | MedicationRequest.dosageInstruction.doseAndRate.dose    |
| RXE-13            | MedicationRequest.requester                             |

## PDS enrichment

When PID-3 contains an `NHS` identifier, the transform service calls the `pds-client` service before persisting. PDS fields overlay the locally-derived Patient resource for `name`, `gender`, `birthDate`, `address`, `telecom`. Local identifiers (the trust MRN) are always preserved.

If PDS is unreachable or the circuit breaker is open, we fail open — the pipeline continues with locally-derived demographics and an alert fires. Patient safety is better served by best-effort enrichment than by dropping the message.

## Bundle shape

All resources for a message are packaged as a FHIR `transaction` Bundle with `urn:uuid:` fullUrl references between them. HAPI FHIR resolves the references and rewrites them to concrete resource ids on persist, giving us atomic writes per message.
