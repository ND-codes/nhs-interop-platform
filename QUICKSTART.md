# Quickstart — from zero to a running demo in 5 minutes

## Prerequisites

- Docker Desktop 24+ running (or equivalent container runtime)
- `make`, `curl`, `git` on your PATH
- 4 GB RAM free for the stack (HAPI FHIR + Postgres are the hungry ones)

Optional for the cloud bits: AWS CLI + Terraform 1.6+, `kubectl`, `helm`.

## Step 1 — Get the code onto your machine

```bash
# If you've cloned from GitHub:
git clone https://github.com/<you>/nhs-interop-platform.git
cd nhs-interop-platform

# Or if you're copying this folder out of the workspace, just cd into it:
cd "nhs-interop-platform"
```

## Step 2 — Create the local env file

```bash
cp .env.example .env.local
# The defaults in .env.example are fine; PDS_STUB=true keeps everything offline.
```

## Step 3 — Build and start the stack

```bash
make demo
```

This will:
1. `docker compose build` all three services.
2. Start ingest, transform, pds-client, HAPI FHIR, Postgres, Prometheus, Grafana.
3. Wait for HAPI FHIR's `/fhir/metadata` to respond (about 30-45 seconds on first boot).
4. Print every URL you need.

## Step 4 — Send a message and see it land in FHIR

```bash
./scripts/send-adt.sh
```

You should see:
- A `200 OK` from ingest with a trace id and `forwarded: true`.
- A FHIR Bundle response with the persisted Patient, Encounter and MessageHeader ids.
- Open <http://localhost:8080/fhir/Patient?identifier=https://fhir.nhs.uk/Id/nhs-number|9000000009> to browse the Patient.

## Step 5 — Send a lab result (ORU message with observations)

```bash
./scripts/send-oru.sh
# Then browse:
open http://localhost:8080/fhir/Observation
```

## Step 6 — Look at the dashboards

- Grafana: <http://localhost:3000> (admin / admin). Dashboard → NHS Interop → HL7 Pipeline.
- Prometheus: <http://localhost:9090>. Try the query `rate(http_requests_total[1m])`.
- Ingest Swagger: <http://localhost:8000/docs>
- Transform Swagger: <http://localhost:8001/docs>
- PDS client Swagger: <http://localhost:8002/docs>

## Step 7 — Run the unit tests

```bash
make test              # runs pytest inside a throwaway container for each service
```

## Step 8 — Run the end-to-end integration test (stack must be up)

```bash
make integration
```

## Step 9 — Tear down

```bash
make down              # stops containers but keeps the Postgres volume
# or
make clean             # stops everything AND deletes the volume (destroys data)
```

## Troubleshooting

| Symptom                                               | Fix                                                                                 |
|-------------------------------------------------------|-------------------------------------------------------------------------------------|
| `make demo` hangs on "Waiting for HAPI FHIR"          | First boot takes up to 90 seconds — wait. If it still hangs, `docker compose logs hapi-fhir`. |
| Ingest returns `forwarded: false`                     | Transform crashed — `docker compose logs transform`. Usually a Python import error. |
| Patient not appearing in FHIR                         | Check the DLQ: `docker compose exec transform ls /tmp/dlq`. Any file there = a mapping error. Tail transform logs for the trace id. |
| Grafana shows "No data"                               | Prometheus needs 30 seconds to scrape. Reload the dashboard. Check <http://localhost:9090/targets> — all three services should be `UP`. |
| Ports already in use                                  | Change the left-hand side of `ports:` in `docker-compose.yml`, e.g. `"18080:8080"`. |

## When you're ready to go cloud

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars   # create and set project + environment
terraform init
terraform plan -out=tfplan
terraform apply tfplan                         # ~15-20 minutes for EKS
```

Then connect to the cluster:

```bash
aws eks update-kubeconfig --name nhs-interop-dev-eks --region eu-west-2
kubectl apply -f k8s/argocd/app-of-apps.yaml
# ArgoCD will pick up the app-of-apps and reconcile each service.
```

