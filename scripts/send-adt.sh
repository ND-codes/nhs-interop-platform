#!/usr/bin/env bash
# Send a sample ADT^A01 message to the ingest service and show the result.
set -euo pipefail

INGEST_URL="${INGEST_URL:-http://localhost:8000/hl7}"
FHIR_URL="${FHIR_URL:-http://localhost:8080/fhir}"

HL7_MSG=$'MSH|^~\\&|CERNER|GENERAL_HOSP|INTEROP|GENERAL_HOSP|20260101120000||ADT^A01|MSG00001|P|2.5\rEVN|A01|20260101120000\rPID|1||9000000009^^^NHS^NH~L12345^^^GENERAL_HOSP^MR||DOE^JOHN^A||19800101|M|||1 NHS WAY^^LONDON^^SW1A 1AA^GBR\rPV1|1|I|WARD1^BED1^ROOM1||||12345^SMITH^JANE|||GEN\r'

echo "==> Posting ADT^A01 to $INGEST_URL"
curl -sS -X POST "$INGEST_URL" \
  -H 'Content-Type: text/plain' \
  --data-binary "$HL7_MSG" | tee /tmp/ingest-response.json
echo

echo "==> Waiting 1 second for async processing..."
sleep 1

echo "==> Querying HAPI FHIR for the Patient by NHS number"
curl -sS "$FHIR_URL/Patient?identifier=https://fhir.nhs.uk/Id/nhs-number|9000000009" | python3 -m json.tool | head -80

echo
echo "Full FHIR server at: $FHIR_URL/Patient"
echo "Open Grafana to see metrics: http://localhost:3000"
