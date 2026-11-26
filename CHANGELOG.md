# Changelog

All notable changes to this project. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Each `part-N` tag marks the code as it stood when that article was published.
Dates are the publication dates.

## [Unreleased]

### Fixed

- `make walkthrough` checks 5 and 6 ran once and gave up. In CI, checks 3 and
  4 pass quickly, so check 5 could run before Prometheus had scraped
  `ingest`, and the `master` build for 1.0.0 failed on that race. Run straight
  after a Prometheus restart, check 6 failed the same way. Both checks now
  retry for up to 30 seconds, and a timeout names the target and Prometheus's
  own error for it.
- Check 5 also passed on an empty target list. Right after Prometheus starts
  it has discovered no targets, and "no target is down" was trivially true. It
  now requires the `ingest`, `transform` and `pds-client` jobs to be present.

## [1.0.0] - 2026-11-25

The end of the seven-part build-in-public series. 1.0.0 marks a complete,
documented, locally runnable pipeline with an honest account of its own
limits. It does not mean production-ready: see "Known limitations" below
before deploying anything.

### Added in this release (`part-7`)

- `ServiceDown`, `ServiceTargetMissing` and `BreakerMetricAbsent` alerts.
  Every previous rule watched a rate, a ratio or a gauge, all of which go
  quiet when a service is down. These watch for absence itself, including the
  case of `pybreaker_state` disappearing, which is the defect Part 4 was
  about. Raised by readers of Part 4.
- `send_resolved: true` on both Alertmanager email receivers. The default for
  `email_configs` is false, so the first night of alert delivery sent FIRING
  mail and never told anyone the incident had ended.
- `docs/ai-usage.md`, recording how Claude Code was used and, specifically,
  which classes of defect it found and which it did not.
- A `walkthrough` CI job that starts the full stack and runs
  `make walkthrough` on every pull request. Raised by a reader of Part 6.
- Two promtool cases asserting that `IngestNoTraffic` fires for a pipeline
  that has never carried traffic, and is still firing more than 24 hours into
  an outage. A "traffic in the last 24 hours" clause, drafted to quiet idle
  demo stacks, would have broken both and did not quiet them, so it was
  rejected. Runbook section 5 shows how to silence the alert on a demo stack.
- This changelog.

### Changed

- `make walkthrough` reports the HTTP status of a failed check and stops at
  once on a 4xx, instead of retrying a malformed query for 30 seconds.
- `docs/runbook.md` gains sections 10 to 12, and section 2 explains why the
  breaker needs traffic to close and records that there was no resolution
  email.
- `docs/lessons-learnt.md` corrects a `default_tags` claim that was false for
  most of the project's life.

### Fixed

- Every shell script was stored without the executable bit, so `make
  walkthrough`, `break-pds`, `restore-pds`, `break-fhir` and `restore-fhir`
  failed with "Permission denied" on any Linux or macOS clone. Git Bash on
  Windows ignores the bit, which hid it. Found by the `walkthrough` CI job on
  its first run.

## [0.6.0] - 2026-11-18 (`part-6`)

### Fixed

- `make test` and `make lint` called `cygpath`, which exists only in Git Bash
  on Windows, so both failed on macOS and Linux.
- `make integration` installed into the system interpreter, which pip refuses
  on any PEP 668 externally managed Python.
- `infra/terraform/terraform.tfvars.example` did not exist, and the quickstart
  told you to copy it.
- Alerts reached nobody. No `alerting` block in `prometheus.yml`, no
  Alertmanager running, and a smarthost naming a service nothing started.
  Raised by a reader of Part 4.
- Four stale claims in `docs/architecture.md`: dead-lettering to S3, GitLab
  CI, CI pushing images to a registry, and distributed tracing that does not
  exist.
- A raw `|` in the walkthrough's FHIR query, which RFC 3986 does not permit
  and Tomcat rejects.

### Added

- `alertmanager` and `mailpit` services, so an alert produces a notification
  you can open and read.
- `make walkthrough`, which runs the documented tour as eight assertions.

## [0.5.0] - 2026-11-11 (`part-5`)

### Fixed

- The NHS number was logged at INFO in four places in `pds-client` and sent
  upstream as an `X-Request-ID`. It is now never logged, and the trace id
  `transform` already sent, and the handler had been discarding, is used
  instead.
- `transform` logged `str(exc)` on a failed PDS lookup, and an
  `HTTPStatusError` message embeds the request URL, which contains the NHS
  number.
- `pds-client` carried the same coroutine breaker defect fixed in `transform`
  for Part 4.
- The dead-letter path had no retention. It now has a 72 hour window, pruning,
  an exported age and an alert.
- `POST /hl7` read its body with no size limit.
- Terraform `default_tags` were commented out while the documentation claimed
  `DataClass=patient-identifiable` reached every resource.
- All eight existing alert runbook links were dead and always had been.

### Removed

- `.gitlab-ci.yml`, cited four times as audit evidence for a pipeline that
  could not run, because the repository is on GitHub.

## [0.4.0] - 2026-11-04 (`part-4`)

### Added

- `pybreaker_state`, `hl7_field_dropped_total`, `nhs_number_invalid_total` and
  `patient_write_total`, with Grafana panels for each.
- NHS number Modulus 11 validation, which the NHS Data Dictionary makes
  mandatory and which nothing had been doing.
- `alerts_test.yml`, including a case asserting that a rule with no series
  exported is silent, which is the state this repository was in.
- `scripts/break-pds.sh` and `scripts/break-fhir.sh`, to drive real failures.

### Fixed

- `PDSCircuitOpen` had watched a metric no service exported since the first
  week, so it could never fire.
- The PDS circuit breaker was applied as a decorator to a coroutine function
  and so never opened. The dead-letter volume was root-owned and every write
  failed silently.

## [0.3.0] - 2026-10-28 (`part-3`)

### Fixed

- Nine HL7 v2 to FHIR mapping defects: OBX value types, unconditional POST
  where a conditional update was needed, partial and offset birth dates,
  undecoded HL7 escape sequences, the dropped middle name, non-RFC-4122
  `fullUrl` values, nulls and empty strings that FHIR forbids, PID-8 sending A
  and N to `unknown` rather than `other`, and a test that asserted the bug.
- `docs/hl7-fhir-mapping.md`, which documented three mappings that were never
  implemented.

## [0.2.0] - 2026-10-21 (`part-2`)

### Added

- GitHub Actions: lint, tests, gitleaks, Semgrep, Trivy image and config
  scans, Syft SBOMs, and Terraform `validate` and `fmt -check`.

## [0.1.0] - 2026-10-14 (`part-1`)

### Added

- The baseline described in Part 1: ingest, transform and pds-client, HL7 v2
  to FHIR R4 mapping, HAPI FHIR on Postgres, Prometheus and Grafana, Terraform
  for EKS, Helm charts and Argo CD manifests.

## Known limitations at 1.0.0

These are deliberate and documented rather than oversights.

- `POST /hl7` has no authentication.
- No part of the AWS configuration has ever been applied. CI runs
  `terraform validate` and `fmt -check` against a provider with fake
  credentials, and nothing else.
- The dead-letter path is local and does not survive a pod restart. The S3
  bucket with the correct retention controls exists and nothing writes to it.
- There is no Alertmanager in the Helm charts, so Kubernetes alert delivery
  does not exist. Local delivery does.
- Patient identity is keyed on the NHS number alone, which is unsafe for the
  cases Kevin Mayfield described: a CHI number arriving in the NHS Number
  field, and the number changing when a patient moves across the border.
  Weighted matching is the fix and is not built.
- The ingest to transform hop is synchronous HTTP. A queue is the first thing
  to replace.

`docs/security.md` holds the full "Claimed, not built" list.

[Unreleased]: https://github.com/ND-codes/nhs-interop-platform/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/ND-codes/nhs-interop-platform/releases/tag/v1.0.0
[0.6.0]: https://github.com/ND-codes/nhs-interop-platform/releases/tag/part-6
[0.5.0]: https://github.com/ND-codes/nhs-interop-platform/releases/tag/part-5
[0.4.0]: https://github.com/ND-codes/nhs-interop-platform/releases/tag/part-4
[0.3.0]: https://github.com/ND-codes/nhs-interop-platform/releases/tag/part-3
[0.2.0]: https://github.com/ND-codes/nhs-interop-platform/releases/tag/part-2
[0.1.0]: https://github.com/ND-codes/nhs-interop-platform/releases/tag/part-1
