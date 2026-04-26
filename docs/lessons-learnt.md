# Lessons learnt

A running retrospective across the build of this platform. The point isn't to be triumphalist — it's to capture the things that tripped me, the things that worked better than expected, and the things a future version of me (or a teammate picking this up) would want to know.

Written in the first person on purpose. I've seen too many "post-mortems" buried in passive voice; the honest ones land better.

---

## Day 1 — Foundation and local demo

### What worked

**Stratifying the git history into focused commits was worth the extra keystrokes.** The difference between one `initial commit` and nine conventional-commit steps isn't cosmetic — it's the difference between "here is a pile of code" and "here is a thought process". Readers can jump to the commit where I added observability and read only those files. Reviewers can rebase cleanly. Future me can `git bisect` a regression back to a 60-line change instead of a 4,000-line dump.

**Starting with `/healthz` and `/readyz` on every service.** Kubernetes probes are not an afterthought — if I bolt them on at the end, the code around them always ends up ugly. Starting with them forced me to think about *when* a service is ready to receive traffic vs *whether* it's alive. For `ingest`, ready means the downstream HTTP client is initialised; for `transform`, ready means we've connected to HAPI; for `pds-client`, ready is trivially true because it doesn't own state. That's three subtly different meanings for the same probe, and encoding the difference up-front paid off.

**Multi-stage Docker builds from the start.** The runtime image is a `python:3.12-slim` with only the installed packages and the application code — no build toolchain, no source of `requirements.txt`, no compiler. Trivy is quieter, the image is smaller, the attack surface is smaller. One interviewer in the past has asked me *"what's in your final image that doesn't need to be there?"* and being able to answer *"nothing"* is worth the 40 lines of Dockerfile.

**Docker Compose health checks with `depends_on: condition: service_healthy`.** This is the difference between `docker compose up` working and `docker compose up` being a flaky race condition. Without it, the transform service tried to talk to HAPI FHIR before HAPI had finished initialising its Postgres schema — the first run in ten would fail. With it, every run is deterministic.

### What didn't work first time

**HL7 v2 segment terminators.** HL7 v2 uses carriage returns (`\r`) as segment terminators, but every tool I used to craft test messages converted them silently to `\n` or `\r\n`. The first version of `mapper._parse` segfaulted on anything I piped through a heredoc or saved through an editor. The fix was a one-liner — `raw.replace("\r\n", "\r").replace("\n", "\r")` — but the lesson is bigger: **real HL7 messages in the wild arrive with three different line-ending conventions** and any parser that assumes one of them is fragile. Defensive normalisation at the entry point, then everything downstream can assume a canonical form.

**The `hl7` Python library has a surprising API for repeating fields.** PID-3 (patient identifiers) can contain multiple repetitions, each with multiple components. The naive `pid[3]` doesn't tell you which shape you have — it could be a single scalar, a list of scalars, a list of component tuples, or a list of lists. I ended up with a `_field` helper that tries increasingly specific accessors and falls back to string coercion. It's not elegant, but it doesn't crash on real-world messages.

**FHIR transaction bundles and `urn:uuid:` references.** The first version of the mapper used placeholder strings like `"Patient/1"` for internal references. HAPI FHIR accepted the write but the Encounter's `subject.reference` pointed at a nonexistent Patient — because HAPI had rewritten the Patient's id on persist. The fix is `urn:uuid:` references with a matching `fullUrl` in each entry; HAPI resolves them atomically. This is exactly the kind of bug that only shows up when you actually query the resulting bundle, not when you just watch the write succeed.

### What I'd do differently if I started again

**I'd write the DLQ path before the happy path.** I wrote `map_message` first, saw it work, then bolted on the DLQ. That ordering meant the first two commits of the transform service didn't have a failure mode — which isn't how real code lives. Starting with *"every message either succeeds, or lands here with a reason"* would have made the happy path cleaner.

**I'd use SQS from the start, not in-memory queues.** In-memory works for the demo but hides the actual message-ordering question (SQS FIFO vs standard). Swapping now is annotated in the roadmap but means I can't currently demonstrate at-least-once semantics under partition. This is the "production-shaped, not production" trade-off I made consciously to stay within the build budget.

---

## Day 2 — Observability, PDS, infrastructure

### What worked

**Writing the alerts before the dashboards.** My instinct was the reverse — build pretty dashboards, then figure out what to alert on. Doing it the other way round forced me to ask "what does failure actually look like here?" first, and the dashboards fell out naturally once the alert queries existed. Every alert maps to a runbook section, and every dashboard panel exists because it answers one of those alerts.

**Circuit breaker on PDS from day one.** The instinct under time pressure is to "just make it work" and worry about resilience later. PDS is an external dependency I don't control — NHS sandbox environments have outages, rate limits, and authentication rollovers. Putting `pybreaker` in from the start meant I got the fail-open behaviour for free, and the decision *"what does the pipeline do when PDS is down"* was answered in code before it was ever asked in anger.

**Terraform `default_tags` at the provider level.** Every resource inherits `Project`, `Environment`, `Owner`, `DataClass=patient-identifiable`. That last tag is how a DSPT auditor can trace patient data through the account: `aws resourcegroupstaggingapi get-resources --tag-filters Key=DataClass,Values=patient-identifiable` returns everything that needs extra scrutiny. One tag, one audit query, two lines of Terraform.

### What didn't work first time

**EKS managed add-on version conflicts.** I initially pinned `coredns`, `kube-proxy`, `vpc-cni` and the EBS CSI driver to specific versions. Terraform apply then failed on a version mismatch every time I bumped the cluster version. The fix was `most_recent = true` on the add-ons, with cluster version pinned — so the cluster controls the envelope and the add-ons follow. The broader lesson: **pin what you own, trust what AWS owns.**

**IRSA service account namespace mismatch.** My Helm chart annotation pointed at `namespace: default`, but my IAM policy's trust relationship only permitted the `interop` namespace. Pods came up, got 403s from Secrets Manager, and I spent 20 minutes confused. Lesson: the IAM trust policy and the ServiceAccount annotation have to agree on namespace, service account name, and OIDC provider. Document the tuple explicitly in the README.

**Prometheus metric naming collisions between `prometheus_client` and `prometheus-fastapi-instrumentator`.** If you `Counter("hl7_dlq_total", ...)` in a module that's imported before the instrumentator registers, you can get a double-registration panic. The fix is module-level constants and making sure the instrumentator is the last thing to touch the default registry.

### What I'd do differently

**I'd treat the Terraform code as a module from the start.** It's currently a flat set of `.tf` files, which is fine for a demo but doesn't reflect how I'd ship it. A real version would be split into `modules/vpc`, `modules/eks`, `modules/rds` with each having its own `variables.tf` and `outputs.tf`, and a thin root module that wires them together. Then `terraform-docs` generates the READMEs automatically and each module can be versioned independently in its own registry.

**I'd enable OpenTelemetry tracing earlier.** I've annotated it in the architecture diagram but not wired it up in code. The RED metrics are fine for "is the system healthy?" but not sufficient for "why is this specific request slow?" — distributed tracing is the answer, and retrofitting it across three services is slower than adding it at build time.

---

## Day 3 — Docs, polish, and review

### What worked

**Writing the runbook before the incident.** I know. That's what everyone says. But the act of writing `docs/runbook.md` surfaced three alerts I hadn't defined yet (`IngestNoTraffic`, `DLQRateHigh`, `PDSCircuitOpen`) because I couldn't describe the response without first defining the signal. Runbook → alerts is a more honest flow than alerts → runbook.

**Writing the security.md as a controls-to-threats matrix.** Security docs that lead with "we use encryption" are useless to auditors. Starting from "here are the threats we considered, here is the control, here is the evidence file or code path" — that's a document a DSPT auditor can actually work through. Same doc, shifted framing, enormously more useful.

### What I'd do differently

**Start the lessons-learnt doc on Day 1, not Day 3.** This file should have been a running log, not a retrospective. There are details from Day 1 I've already half-forgotten and had to reconstruct. A five-minute end-of-day note would have captured them at full fidelity.

---

## Technical gotchas — a permanent index

These are the things I had to discover the hard way. If you're reading this to pick up the repo, read these first and save yourself the time.

### HL7 v2 parsing
- Line endings: always normalise `\r\n` and `\n` to `\r` before parsing.
- `pid[3]` can be one identifier, a list, or a list of tuples. Assume the worst case.
- Empty fields in HL7 are `""`, never `None`. `_field()` returns `None` for both.
- Not every ADT message has a PV1 segment. Don't crash.
- Not every message has an NHS number. Skip PDS enrichment silently, don't error.
- HL7 timestamps come in `YYYYMMDD`, `YYYYMMDDHHMM`, and `YYYYMMDDHHMMSS` flavours. Try each.

### FHIR R4
- Use `transaction` bundles, not `batch`, for atomicity across resources.
- Use `urn:uuid:` fullUrls for internal references; HAPI rewrites them on persist.
- HAPI is forgiving about schema violations but PDS sandbox is not. Validate against the R4 profile before calling PDS.
- HAPI FHIR's first boot takes 45–90 seconds. Wait for `/fhir/metadata` to 200, don't just wait a fixed time.

### Kubernetes / EKS
- IRSA = IAM trust policy namespace + ServiceAccount annotation + OIDC provider. All three must agree.
- `runAsNonRoot: true` conflicts with base images that don't have a non-root user. Always add one in the Dockerfile.
- `readOnlyRootFilesystem: true` requires an `emptyDir` mounted at `/tmp` for almost everything. Don't forget.
- NetworkPolicy `egress` needs an explicit allow for CoreDNS (`kube-system:53/UDP`). Without it, pods can't resolve `Service` DNS names.
- `topologySpreadConstraints` with `whenUnsatisfiable: ScheduleAnyway` is safer than `DoNotSchedule` — the latter can block scale-up when AZs are under capacity.

### Terraform / AWS
- `enable_flow_log` on the VPC module is mandatory for DSPT audit.
- S3 Object Lock can only be enabled at bucket creation. If you forget, you recreate. Don't forget.
- RDS `deletion_protection = true` in prod means `terraform destroy` fails — that's the point. Use a conditional to keep dev cheap.
- KMS CMKs have a 14-day minimum deletion window. Plan accordingly.
- `aws_db_instance.kms_key_id` requires the CMK ARN, not the alias. This one bites people a lot.

### CI/CD
- Trivy should fail the build on HIGH/CRITICAL. Don't use `--exit-code 0` to "unblock" — it'll always be unblocked.
- Semgrep's `p/ci` ruleset catches most of what you need; the `p/security-audit` pack adds noise but catches real issues.
- Gitleaks should run on every push, not just MR. Bad commits are often fixed by force-pushing on the feature branch — but the secret is already in the reflog.
- SBOMs should be per-image and kept for 90 days minimum. DSPT audit will ask.

---

## Operational learnings

**Runbooks beat postmortems.** An incident with a runbook entry resolves in 15 minutes; without one, 90. The ratio is roughly constant across severities. Budget time to write runbook entries as you build alerts, not after an incident forces it.

**Blameless retrospectives surface the real causes.** My early retros were "I fixed this" write-ups. They were useless because they skipped the "why was this possible in the first place?" step. The question "what made it easy to do the wrong thing?" is more valuable than "who did the wrong thing?" every single time.

**Supplier assurance is a first-class engineering concern, not a legal one.** SBOMs, image signing, pen-test evidence, exit plans — these land in the infrastructure repo, get tested in CI, and block releases when they fail. Treating them as compliance paperwork is how organisations end up with supply-chain incidents.

---

## What I still want to build

If I had another week:

1. **Replace the in-memory queue with SQS FIFO.** This is the first question every senior interviewer asks, and the answer is always "I know — here's the annotated swap point."
2. **Add OpenTelemetry distributed tracing.** RED metrics are necessary but not sufficient; a trace across ingest → transform → PDS → HAPI is how you diagnose the slow outlier.
3. **Wire up External Secrets Operator.** PDS API keys come out of Secrets Manager at pod-startup time, rotation handled automatically.
4. **Terraform module extraction.** Break the flat `infra/terraform/` into reusable modules with independent versioning.
5. **Contract tests against the real PDS sandbox.** Pact or similar — prove the integration shape is still valid when NHS updates the sandbox.
6. **Chaos testing.** Kill the transform pod mid-message and prove the DLQ catches it. Kill Postgres for 30 seconds and prove we queue + replay cleanly.
7. **Azure parity.** Document the AKS / Azure Managed Postgres equivalent for the case where a trust requires Azure for sovereignty reasons.

Each of these is in the roadmap in the README. Each is a concrete next step, not a platitude.
