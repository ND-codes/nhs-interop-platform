#!/usr/bin/env bash
# Undo scripts/break-fhir.sh. Dead-lettered messages are not replayed: there
# is no replay tooling in this repo yet, which is itself worth knowing.
set -euo pipefail

echo "==> Starting hapi-fhir"
docker compose start hapi-fhir

echo "==> Waiting for it to answer (can take ~45 seconds)"
until curl -sf http://localhost:8080/fhir/metadata >/dev/null 2>&1; do sleep 2; done
echo "    ready"
echo

echo "The dead-lettered messages are still in /tmp/dlq inside the transform"
echo "container. Nothing replays them automatically. To see what is waiting:"
echo "  docker compose exec transform ls -la /tmp/dlq"
