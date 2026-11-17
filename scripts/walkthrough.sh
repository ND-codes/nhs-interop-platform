#!/usr/bin/env bash
# The ten-minute tour, as an assertion rather than a list of instructions.
#
# QUICKSTART.md describes this sequence in prose. This script runs it and fails
# on the first step that does not produce what the document claims, so the two
# cannot drift apart without someone noticing. Run it after `make demo`.
#
# It is deliberately read-only apart from the two messages it sends, and it
# does not break anything: the failure demonstrations stay in `make break-pds`
# and `make break-fhir`.
set -euo pipefail

INGEST=${INGEST_URL:-http://localhost:8000}
FHIR=${FHIR_URL:-http://localhost:8080/fhir}
PROM=${PROM_URL:-http://localhost:9090}
ALERTMANAGER=${ALERTMANAGER_URL:-http://localhost:9093}
MAILPIT=${MAILPIT_URL:-http://localhost:8025}
NHS_NUMBER=${NHS_NUMBER:-9000000009}

step=0
say() { step=$((step + 1)); printf '\n[%d/8] %s\n' "$step" "$1"; }
ok()   { printf '      ok   %s\n' "$1"; }
fail() { printf '      FAIL %s\n' "$1" >&2; exit 1; }

# Retry because HAPI FHIR indexes asynchronously and Prometheus scrapes on a
# 10 second interval. Without this the tour is flaky in a way that reads as a
# broken pipeline.
wait_for() {
  local what=$1 tries=$2 cmd=$3 i
  for ((i = 1; i <= tries; i++)); do
    if eval "$cmd" >/dev/null 2>&1; then return 0; fi
    sleep 2
  done
  fail "$what did not become true within $((tries * 2))s"
}

say "The stack is running"
docker compose ps --status running --format '{{.Service}}' > /tmp/wt-services
for svc in ingest transform pds-client hapi-fhir prometheus alertmanager mailpit; do
  grep -qx "$svc" /tmp/wt-services || fail "$svc is not running. Try: make demo"
done
ok "$(wc -l < /tmp/wt-services) services up"

say "Ingest accepts an ADT message"
code=$(curl -s -o /tmp/wt-adt -w '%{http_code}' -X POST "$INGEST/hl7" \
  --data-binary @tests/fixtures/hl7/adt_a01.hl7 -H 'Content-Type: text/plain')
case "$code" in
  200 | 202) ok "HTTP $code" ;;
  *) fail "ingest returned HTTP $code: $(cat /tmp/wt-adt)" ;;
esac

say "The Patient reaches FHIR, keyed on the NHS number"
query="$FHIR/Patient?identifier=https://fhir.nhs.uk/Id/nhs-number%7C$NHS_NUMBER"
# Parsed rather than grepped: the Bundle's whitespace is not guaranteed, and a
# grep for "total" would also match a zero.
wait_for "the Patient" 15 "curl -sf '$query' | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get(\"total\", 0) > 0 else 1)'"
ok "found at $query"

say "A lab result produces Observations"
curl -sf -o /dev/null -X POST "$INGEST/hl7" \
  --data-binary @tests/fixtures/hl7/oru_r01.hl7 -H 'Content-Type: text/plain' \
  || fail "ingest rejected the ORU message"
wait_for "an Observation" 15 "curl -sf '$FHIR/Observation' | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get(\"total\", 0) > 0 else 1)'"
ok "Observations present"

say "Prometheus is scraping every service"
down=$(curl -sf "$PROM/api/v1/targets" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin)["data"]["activeTargets"]; print(" ".join(t["labels"].get("job","?") for t in d if t["health"]!="up"))')
[ -z "$down" ] || fail "targets not up: $down"
ok "all targets up"

# This is the step that did not exist before Part 6. Prometheus evaluated every
# rule and notified nobody, because it had no alerting block and nothing ran an
# Alertmanager. A rule that is Firing in a UI is not an alert.
say "Prometheus knows where to send an alert"
discovered=$(curl -sf "$PROM/api/v1/alertmanagers" \
  | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["data"]["activeAlertmanagers"]))')
[ "$discovered" -ge 1 ] || fail "Prometheus has no active Alertmanager. Check the alerting block in observability/prometheus/prometheus.yml"
ok "$discovered Alertmanager discovered"

say "Alertmanager is up and holding its config"
curl -sf "$ALERTMANAGER/api/v2/status" >/dev/null || fail "Alertmanager is not responding on $ALERTMANAGER"
ok "Alertmanager healthy"

say "The inbox that alerts land in is reachable"
curl -sf "$MAILPIT/api/v1/messages" -o /tmp/wt-mail \
  || fail "Mailpit is not responding on $MAILPIT"
# Reachability is the assertion. The count is read defensively and reported
# only if the payload offers something countable, so a change in Mailpit's
# response shape cannot turn a working inbox into a failed tour.
held=$(python3 "$(dirname "$0")/_count_messages.py" /tmp/wt-mail)
ok "inbox reachable, $held message(s) held"

cat <<EOF

The tour completed. Everything QUICKSTART.md claims, this run proved.

  FHIR Patient   $FHIR/Patient?identifier=https://fhir.nhs.uk/Id/nhs-number%7C$NHS_NUMBER
  Grafana        http://localhost:3000  (admin / admin)
  Prometheus     $PROM/alerts
  Alertmanager   $ALERTMANAGER
  Mail inbox     $MAILPIT

To see an alert travel the whole way, run:

  make break-pds     # then wait about two minutes
  open $PROM/alerts  # PDSCircuitOpen goes Firing
  open $MAILPIT      # the notification arrives here
  make restore-pds

EOF
