#!/bin/bash
# Test script for NHS Interop Platform services

set -e

SERVICES="ingest transform pds-client"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for svc in $SERVICES; do
    echo "==> Testing services/$svc"
    docker run --rm \
        -v "$PROJECT_DIR/services/$svc:/app" \
        -w /app \
        python:3.12-slim \
        sh -c "pip install -q -r requirements-dev.txt && pytest -q"
done

echo "==> All tests passed!"
