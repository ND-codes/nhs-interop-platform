#!/usr/bin/env bash
# Dead-letter messages on purpose, so DLQRateHigh can be watched firing.
#
# The plan for this article said "stop the transform service mid-burst and
# show the DLQ". That does not work: the dead-letter folder is written by
# transform, so stopping transform means nothing writes to it. Ingest simply
# logs the failure and answers 200 with forwarded:false. To fill the DLQ you
# have to break the thing transform writes to, which is HAPI FHIR.
#
# Undo with: ./scripts/restore-fhir.sh
set -euo pipefail

INGEST_URL="${INGEST_URL:-http://localhost:8000/hl7}"
PROM_URL="${PROM_URL:-http://localhost:9090}"
TRANSFORM_URL="${TRANSFORM_URL:-http://localhost:8001}"
BURST="${BURST:-10}"
GAP="${GAP:-3}"

HL7_MSG=$'MSH|^~\\&|CERNER|GENERAL_HOSP|INTEROP|GENERAL_HOSP|20260101120000||ADT^A01|MSG00001|P|2.5\rEVN|A01|20260101120000\rPID|1||9000000009^^^NHS^NH~L12345^^^GENERAL_HOSP^MR||DOE^JOHN^A||19800101|M|||1 NHS WAY^^LONDON^^SW1A 1AA^GBR\rPV1|1|I|WARD1^BED1^ROOM1||||12345^SMITH^JANE|||GEN\r'

need() { command -v "$1" >/dev/null 2>&1 || { echo "This script needs $1 on PATH."; exit 1; }; }
need curl
need docker

dlq_total() {
  curl -sf "$TRANSFORM_URL/metrics" 2>/dev/null \
    | awk '/^hl7_dlq_total\{reason="fhir_write_error"\}/ {print $2; found=1} END {if (!found) print 0}'
}

if ! curl -sf "$TRANSFORM_URL/healthz" >/dev/null 2>&1; then
  echo "transform is not answering on $TRANSFORM_URL. Run 'make demo' first."
  exit 1
fi

echo "==> Dead letters before: $(dlq_total)"
echo
echo "==> Stopping hapi-fhir"
docker compose stop hapi-fhir
echo

echo "==> Sending $BURST messages. Each one retries three times before giving up,"
echo "    so this is slower than it looks."
for i in $(seq 1 "$BURST"); do
  code=$(curl -sS -o /dev/null -w '%{http_code}' \
    -X POST "$INGEST_URL" -H 'Content-Type: text/plain' --data-binary "$HL7_MSG")
  printf '    message %d: ingest returned HTTP %s   dead letters: %s\n' \
    "$i" "$code" "$(dlq_total)"
  sleep "$GAP"
done
echo

echo "==> Dead letters after: $(dlq_total)"
echo
echo "Note what ingest returned. The front door answers 200 with"
echo "forwarded:false, because transform replied 502 and nothing retries at"
echo "that hop. The messages are safe in the dead-letter folder, and the"
echo "sender has no idea anything went wrong. That gap is Part 6."
echo
echo "Inspect the folder:"
echo "  docker compose exec transform ls -la /tmp/dlq"
echo
echo "Where to look:"
echo "  Prometheus alerts   $PROM_URL/alerts    DLQRateHigh fires after 5 minutes"
echo "  Grafana             http://localhost:3000  panel 'HL7 dead-letter rate'"
echo
echo "Put it back with: ./scripts/restore-fhir.sh"
