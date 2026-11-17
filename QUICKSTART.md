# The tour: from a clone to a FHIR Patient, and an alert that reaches someone

Every command here is run by `make walkthrough`, which fails on the first step
that does not produce what this document claims. If the tour and the repository
drift apart, that target is what notices.

Times below are from the run described at the end. Your first run will be
slower, because it pulls images.

## What you need

- Docker Desktop 24 or later, running, with about 4 GB free. HAPI FHIR and
  Postgres are the hungry ones.
- `make`, `curl`, `git` and `python3` on your PATH.

That is all. Nothing reaches the internet except the image pulls: `PDS_STUB`
defaults to true, so the PDS lookup returns a canned record and the NHS Spine
sandbox is never called.

The cloud section at the end also wants Terraform 1.6+, `kubectl` and `helm`,
and an AWS account. Nothing in this repository has ever been applied to one.

## 1. Clone and configure

```bash
git clone https://github.com/ND-codes/nhs-interop-platform.git
cd nhs-interop-platform
cp .env.example .env.local
```

The defaults in `.env.example` are the demo settings and need no editing.
`.env.local` is git-ignored.

## 2. Start the stack

```bash
make demo
```

That builds the three services and starts nine containers: ingest, transform,
pds-client, HAPI FHIR, Postgres, Prometheus, Grafana, Alertmanager and Mailpit.
It waits for HAPI FHIR's `/fhir/metadata` to answer, which is 30 to 45 seconds
on a warm machine and longer on the first run, then prints every URL.

## 3. Run the tour

```bash
make walkthrough
```

Eight checks. It sends an ADT message and waits for the Patient to appear in
FHIR, sends a lab result and waits for the Observations, confirms Prometheus is
scraping every service, and confirms that Prometheus has actually discovered an
Alertmanager to send to. The last one is there because for two months it would
have failed.

If a step fails, it says which and stops. Everything after that point is
unverified, so do not read a partial run as a partial success.

## 4. Look at what it produced

```bash
# the Patient, keyed on the NHS number
open "http://localhost:8080/fhir/Patient?identifier=https://fhir.nhs.uk/Id/nhs-number%7C9000000009"

# the lab results
open http://localhost:8080/fhir/Observation
```

- Grafana: <http://localhost:3000>, admin / admin. The HL7 Pipeline dashboard
  is provisioned, so it is already there under Dashboards.
- Prometheus: <http://localhost:9090>, and <http://localhost:9090/alerts> for
  the rules.
- Swagger for each service: <http://localhost:8000/docs>,
  <http://localhost:8001/docs>, <http://localhost:8002/docs>.

## 5. Break it on purpose

This is the part worth your time, and the part a walkthrough usually skips.

```bash
make break-pds
```

That stops `pds-client` and pushes eight messages through, two seconds apart,
printing the circuit breaker gauge after each one. The breaker opens. Then:

- <http://localhost:9090/alerts> shows `PDSCircuitOpen` go Pending and then
  Firing, about two minutes in.
- <http://localhost:8025> is the inbox the notification arrives at. This is
  Mailpit, a disposable SMTP sink that holds mail in memory and forwards it
  nowhere.

The messages keep flowing while the breaker is open. That is deliberate:
enrichment fails open, so a PDS outage degrades data quality rather than
dropping clinical messages. Watch the Patient still being written, with
locally derived demographics only.

```bash
make restore-pds
```

For the dead-letter path instead:

```bash
make break-fhir     # stops HAPI FHIR, so writes fail and messages dead-letter
docker compose exec transform ls /tmp/dlq
make restore-fhir
```

Note that stopping `transform` would not demonstrate this, because `transform`
is what writes the dead-letter path.

## 6. Run the tests

```bash
make test           # unit tests for each service, in a throwaway container
make lint           # ruff 0.6.9, the version CI pins
make alerts         # promtool: the rules parse, and the rule unit tests pass
make integration    # end to end, needs the stack up
```

`make test` and `make lint` run each service in a `python:3.12-slim` container,
so they need Docker but not a local Python environment. `make integration`
builds a throwaway virtualenv in `.venv-integration`.

## 7. Stop

```bash
make down     # stops containers, keeps the Postgres volume
make clean    # also deletes the volumes, destroying the data
```

## If something goes wrong

| Symptom | What it is |
|---|---|
| `make demo` sits on "Waiting for HAPI FHIR" | First boot can take 90 seconds. If it is still going after that, `docker compose logs hapi-fhir`. |
| Ingest returns `forwarded: false` | Transform is not answering. `docker compose logs transform`. |
| The Patient never appears | Look in the dead-letter path: `docker compose exec transform ls /tmp/dlq`. A file there is a mapping failure, and the filename is the trace id to grep the logs for. |
| Grafana shows No data | Prometheus scrapes every 10 seconds. Give it a moment, then check <http://localhost:9090/targets>. |
| `make walkthrough` fails on the Alertmanager step | Prometheus has not discovered one. Check the `alerting` block in `observability/prometheus/prometheus.yml` and that the alertmanager container is running. |
| A port is already in use | Change the left-hand side of the `ports:` mapping in `docker-compose.yml`. |
| `cygpath: command not found` | You are on a version from before November 2026. `make test` and `make lint` only worked in Git Bash on Windows until then. |

## The cloud part, which has never been run

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform plan -out=tfplan
```

Stop and read the plan. `terraform apply` builds an EKS cluster and an RDS
instance, which cost real money per hour, and takes 15 to 20 minutes.

No part of this configuration has ever been applied. CI runs
`terraform init -backend=false`, `validate` and `fmt -check` with a provider
configured with fake credentials, and nothing else. Treat the AWS material as
reviewed code rather than as a running system, and read `docs/security.md`
before you believe any control described in it is operating.

## How long the tour takes

The full sequence, from `make demo` on a machine that already has the images
through to the end of `make walkthrough`, is the number worth knowing, and it
is the one claim in this document I have not measured on a second machine. If
you time it, the figure in this section should be yours rather than mine.
