#!/usr/bin/env bash
# Send a sample ORU^R01 (lab result) message with two observations.
set -euo pipefail

INGEST_URL="${INGEST_URL:-http://localhost:8000/hl7}"

HL7_MSG=$'MSH|^~\\&|LAB|GENERAL_HOSP|INTEROP|GENERAL_HOSP|20260101140000||ORU^R01|MSG00002|P|2.5\rPID|1||9000000009^^^NHS^NH||DOE^JOHN^A||19800101|M\rOBX|1|NM|8867-4^Heart rate^LN||72|bpm||||F|||20260101140000\rOBX|2|NM|8310-5^Body temperature^LN||37.1|Cel||||F|||20260101140000\r'

echo "==> Posting ORU^R01 to $INGEST_URL"
curl -sS -X POST "$INGEST_URL" \
  -H 'Content-Type: text/plain' \
  --data-binary "$HL7_MSG"
echo
