#!/usr/bin/env bash
# Undo scripts/break-pds.sh and confirm the breaker closes again.
set -euo pipefail

INGEST_URL="${INGEST_URL:-http://localhost:8000/hl7}"
TRANSFORM_URL="${TRANSFORM_URL:-http://localhost:8001}"

HL7_MSG=$'MSH|^~\\&|CERNER|GENERAL_HOSP|INTEROP|GENERAL_HOSP|20260101120000||ADT^A01|MSG00001|P|2.5\rEVN|A01|20260101120000\rPID|1||9000000009^^^NHS^NH~L12345^^^GENERAL_HOSP^MR||DOE^JOHN^A||19800101|M|||1 NHS WAY^^LONDON^^SW1A 1AA^GBR\rPV1|1|I|WARD1^BED1^ROOM1||||12345^SMITH^JANE|||GEN\r'

breaker_state() {
  curl -sf "$TRANSFORM_URL/metrics" 2>/dev/null \
    | awk '/^pybreaker_state\{name="pds"\}/ {print $2; found=1} END {if (!found) print "absent"}'
}

echo "==> Starting pds-client"
docker compose start pds-client

echo "==> Waiting for it to report healthy"
until curl -sf http://localhost:8002/healthz >/dev/null 2>&1; do sleep 2; done
echo "    healthy"
echo

echo "==> Breaker now: $(breaker_state)"
echo "    It stays open until reset_timeout (30s) has passed and one call"
echo "    succeeds, so send traffic to close it."
echo

echo "==> Waiting 31 seconds for the reset timeout"
sleep 31

for i in 1 2 3; do
  curl -sS -o /dev/null -X POST "$INGEST_URL" \
    -H 'Content-Type: text/plain' --data-binary "$HL7_MSG"
  printf '    message %d sent   breaker: %s\n' "$i" "$(breaker_state)"
  sleep 2
done

echo
echo "==> Breaker final: $(breaker_state)   (0 means closed, back to normal)"
