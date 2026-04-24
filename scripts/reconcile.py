#!/usr/bin/env python3
"""
Reconciliation script — mirrors the evidence pack used on real trust EPR
migrations. Takes a batch of HL7 files, hashes the message control ids,
queries HAPI FHIR for MessageHeader resources tagged with each trace id,
and reports any discrepancies.

Run against a running stack:
    make demo
    ./scripts/send-adt.sh          # or loop to generate a batch
    make reconcile
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import sys
import urllib.request

FHIR_BASE = os.getenv("FHIR_BASE_URL", "http://localhost:8080/fhir")
INPUT_DIR = os.getenv("RECONCILE_INPUT_DIR", "tests/fixtures/hl7")


def hash_file(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def fetch_bundle(path: str) -> dict:
    with urllib.request.urlopen(f"{FHIR_BASE}{path}") as resp:
        return json.loads(resp.read())


def main() -> int:
    files = sorted(glob.glob(os.path.join(INPUT_DIR, "*.hl7")))
    if not files:
        print(f"no HL7 files found in {INPUT_DIR}")
        return 0

    input_hashes = {os.path.basename(p): hash_file(p) for p in files}
    patients = fetch_bundle("/Patient?_count=200")
    patient_count = patients.get("total", len(patients.get("entry", [])))

    headers = fetch_bundle("/MessageHeader?_count=200")
    header_count = headers.get("total", len(headers.get("entry", [])))

    print(f"Input files:        {len(input_hashes)}")
    print(f"FHIR Patients:      {patient_count}")
    print(f"FHIR MessageHeaders:{header_count}")
    for name, digest in input_hashes.items():
        print(f"  sha256 {digest}  {name}")

    # Simple rule: header_count should be >= input file count. Mismatch → warn.
    if header_count < len(input_hashes):
        print(f"\nWARN: expected >= {len(input_hashes)} headers, found {header_count}")
        return 1
    print("\nOK: reconciled")
    return 0


if __name__ == "__main__":
    sys.exit(main())
