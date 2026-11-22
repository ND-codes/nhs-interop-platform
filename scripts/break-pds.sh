#!/usr/bin/env bash
# Open the PDS circuit breaker on purpose, so PDSCircuitOpen can be watched
# firing rather than taken on trust.
#
# The pipeline keeps returning 200 throughout. That is the point: enrichment
# has stopped and every request still succeeds.
#
# Undo with: ./scripts/restore-pds.sh
set -euo pipefail

INGEST_URL="${INGEST_URL:-http://localhost:8000/hl7}"
PROM_URL="${PROM_URL:-http://localhost:9090}"
TRANSFORM_URL="${TRANSFORM_URL:-http://localhost:8001}"
BURST="${BURST:-8}"          # fail_max is 5, so 8 leaves headroom
GAP="${GAP:-2}"              # seconds between messages

HL7_MSG=$'MSH|^~\\&|CERNER|GENERAL_HOSP|INTEROP|GENERAL_HOSP|20260101120000||ADT^A01|MSG00001|P|2.5\rEVN|A01|20260101120000\rPID|1||9000000009^^^NHS^NH~L12345^^^GENERAL_HOSP^MR||DOE^JOHN^A||19800101|M|||1 NHS WAY^^LONDON^^SW1A 1AA^GBR\rPV1|1|I|WARD1^BED1^ROOM1||||12345^SMITH^JANE|||GEN\r'

need() { command -v "$1" >/dev/null 2>&1 || { echo "This script needs $1 on PATH."; exit 1; }; }
need curl
need docker

breaker_state() {
  # Read the gauge from the service itself rather than Prometheus, so this
  # works before the first scrape.
  curl -sf "$TRANSFORM_URL/metrics" 2>/dev/null \
    | awk '/^pybreaker_state\{name="pds"\}/ {print $2; found=1} END {if (!found) print "absent"}'
}

if ! curl -sf "$TRANSFORM_URL/healthz" >/dev/null 2>&1; then
  echo "transform is not answering on $TRANSFORM_URL. Run 'make demo' first."
  exit 1
fi

echo "==> Breaker before: $(breaker_state)   (0 closed, 1 open, 2 half-open)"
echo "    If that says 'absent', this build predates the gauge and the alert"
echo "    cannot fire. That was the bug."
echo

echo "==> Stopping pds-client"
docker compose stop pds-client
echo

echo "==> Sending $BURST messages, $GAP second gap. Watch them all succeed."
for i in $(seq 1 "$BURST"); do
  code=$(curl -sS -o /tmp/break-pds-response.json -w '%{http_code}' \
    -X POST "$INGEST_URL" -H 'Content-Type: text/plain' --data-binary "$HL7_MSG")
  printf '    message %d: HTTP %s   breaker: %s\n' "$i" "$code" "$(breaker_state)"
  sleep "$GAP"
done
echo

echo "==> Breaker after: $(breaker_state)"
echo
echo "The pipeline answered 200 every time. PDS enrichment has stopped and the"
echo "stored demographics are now whatever arrived in the message."
echo
echo "Where to look:"
echo "  Prometheus alerts   $PROM_URL/alerts    PDSCircuitOpen goes Pending, then"
echo "                      Firing after 2 minutes (the rule's 'for' duration)."
echo "  Prometheus graph    $PROM_URL/graph?g0.expr=pybreaker_state"
echo "  Grafana             http://localhost:3000  panel 'PDS circuit breaker'"
echo
echo "Half-open (value 2) is transient: pybreaker moves through it inside a"
echo "single call, so a 10 second scrape effectively never catches it. Expect"
echo "the series to sit flat at 1 while pds-client is down."
echo
echo "Put it back with: ./scripts/restore-pds.sh"
