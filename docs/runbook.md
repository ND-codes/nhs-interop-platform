# Runbook

One entry per alert. Each entry has: what fires, what it means, what to check, what to do, what to write up afterwards. If you're reading this at 3am, start with the "Immediate actions" section.

## On-call prerequisites

- AWS SSO access to the target account, role `interop-oncall`.
- `kubectl` context set to the target cluster (`aws eks update-kubeconfig --name nhs-interop-<env>-eks --region eu-west-2`).
- Access to Grafana (`https://grafana.<env>.nhs-interop.example`), Prometheus, and the alerts Slack/PagerDuty queue.

---

## 1. `TransformLatencyHigh`

Link: `docs/runbook.md#1-transformlatencyhigh`

**What fires.** p95 latency on the transform service >1s for 10 minutes.

**What it usually means.** Either PDS is slow (most common), the transform pod is CPU-saturated, or the FHIR server is backpressuring.

**Immediate actions.**
1. Grafana → HL7 Pipeline → PDS lookup latency. If high, the problem is PDS — skip to §2.
2. Grafana → HL7 Pipeline → Requests per second. If traffic has doubled, check the HPA: `kubectl -n interop get hpa`. If it has hit max, scale the cap or add a node group.
3. Grafana → FHIR writes. If failure rate is non-zero, the FHIR backend (RDS) is the bottleneck — check RDS CloudWatch panels for CPU, connection count, and replica lag.

**Mitigation.**
- Temporary scale: `kubectl -n interop scale deploy transform --replicas=<n>`. HPA will fight you — suspend it first with `kubectl autoscale ... --min=<n>`.
- If FHIR writes are failing, inspect the dead-letter path, not a bucket. It
  is a local path in every configuration that runs: a named volume at
  `/tmp/dlq` under Docker Compose, and an `emptyDir` at `/tmp` in Kubernetes.
  The S3 bucket in `infra/terraform/s3.tf` has the right retention and
  encryption controls and nothing writes to it. An `emptyDir` does not survive
  the pod, so a restart or a reschedule during the outage loses the messages,
  and nothing replays automatically: replay is a manual resend from the path.
  Messages older than `DLQ_RETENTION_HOURS`, 72 by default, are pruned.

**Post-incident.** Add the query pattern to the load model. If HPA cap was hit, raise it. If RDS CPU was the cause, schedule an instance-class bump.

---

## 2. `PDSCircuitOpen`

Link: `docs/runbook.md#2-pdscircuitopen`

**What fires.** The PDS circuit breaker has been open for 2 minutes.

**What it means.** The pds-client has seen five consecutive PDS failures. The pipeline is running in *fail-open* mode — messages continue to flow, Patient resources are persisted with locally-derived demographics only. Patient safety is preserved; data quality is temporarily degraded.

**Immediate actions.**
1. `kubectl -n interop logs deploy/pds-client --tail=200` — look for the underlying HTTP error. 401/403 means credentials; 5xx means the sandbox is unhealthy; timeouts mean network.
2. NHS Digital status page: <https://status.digital.nhs.uk/>.
3. If creds are the cause, rotate the API key: fetch from Secrets Manager, redeploy.

**Mitigation.** None is required for patient safety — the breaker is doing its job. Communicate to stakeholders that enriched records from the affected window will need replay once PDS recovers.

**Post-incident.** Replay the DLQ window from S3 against the pipeline once PDS is green. Add a "delta" job if this happens frequently.

---

## 3. `DLQRateHigh`

Link: `docs/runbook.md#3-dlqratehigh`

**What fires.** More than 6 HL7 messages per minute are being dead-lettered.

**What it usually means.** A new upstream has started sending malformed messages, or we have a mapping bug that choked on a field pattern we haven't seen before.

**Immediate actions.**
1. List the most recent dead-lettered messages. They are files in `/tmp/dlq` inside the transform container. Nothing writes to the S3 DLQ bucket, so do not look there.
   - Docker Compose: `docker compose exec transform ls -lt /tmp/dlq | head -20`
   - Kubernetes: `kubectl -n interop exec deploy/transform -- ls -lt /tmp/dlq | head -20`

   Each file is named `<trace_id>.hl7`, so the file name is the trace id for the next step.
2. Inspect the transform logs for the trace id: `kubectl -n interop logs deploy/transform --since=15m | grep <trace_id>`.
3. Classify: is it an upstream bug (wrong field order, unknown segment) or our bug (unhandled edge case in the mapper)?

**Before you change anything in Kubernetes, copy the messages out.** `/tmp` is an `emptyDir`, so the dead-letter path does not survive the pod. A restart, a reschedule or a redeploy deletes every message in it. Copy them first:

`kubectl -n interop cp <transform-pod>:/tmp/dlq ./dlq-<date>`

The copy is complete raw HL7, every PID field included. Keep it on an encrypted disk and delete it once replayed. Messages are also pruned after `DLQ_RETENTION_HOURS` (72 by default), so copy them out before then if the fix will take longer. In Docker Compose the path is a named volume and survives `docker compose restart`, but not `docker compose down -v`.

**Mitigation.**
- Upstream bug: raise a ticket with the sending trust. Keep the copied messages for replay once it is fixed, within the retention window.
- Our bug: copy the messages out, write a failing unit test against the fixture, patch the mapper, redeploy, then replay from the copy.

**Post-incident.** Every mapping bug gets a regression test. The fixture goes into `tests/fixtures/hl7/`.

---

## 4. `TransformErrorRateHigh`

Link: `docs/runbook.md#4-transformerrorratehigh`

**What fires.** Transform 5xx rate >2% for 5 minutes.

**Immediate actions.**
1. Grafana → Errors panel to identify which endpoint is failing.
2. Transform logs: `kubectl -n interop logs deploy/transform --tail=200 | grep -i error`.
3. If it's FHIR write failures, check HAPI FHIR (`kubectl -n interop logs statefulset/hapi-fhir`) and RDS.
4. If it's mapping errors, see §3.

**Mitigation.** Roll back: ArgoCD UI → transform application → history → rollback to last green sync. Or: `kubectl -n interop rollout undo deploy/transform`.

**Post-incident.** Root-cause write-up, failing test added, canary deploy for the next change.

---

## 5. `IngestNoTraffic`

Link: `docs/runbook.md#5-ingestnotraffic`

**What fires.** Zero HL7 messages received for 15 minutes.

**What it usually means.** Either the upstream trust integration engine is down (most common — not our problem to fix, but it's our problem to notice), the ALB has a listener misconfiguration, or our ingress NetworkPolicy is too tight after a recent change.

**Immediate actions.**
1. `curl` the ALB from outside the VPC — if it 503s, it's infra.
2. Check the sending trust's integration team Slack / email.
3. `kubectl -n interop describe networkpolicy` — did anything change in the last 24h?

**Mitigation.** If it's the trust, acknowledge the alert and wait. If it's us, revert the NetworkPolicy change.

---

## 6. `FieldsDroppedRateHigh`

Link: `docs/runbook.md#6-fieldsdroppedratehigh`

**What it means.** Fields present in the incoming message are not reaching the
FHIR resource. The rule watches unparseable dates specifically, because a lost
date of birth is the one that matters most for matching.

**What it does not mean.** The messages are succeeding. This alert fires on a
pipeline that is returning 200 and writing valid Bundles. Nothing is in the
dead-letter folder.

**Triage.**

1. `sum by (segment, field, reason) (rate(hl7_field_dropped_total[15m]))` to see
   which field.
2. `reason="not_mapped"` is expected for PID-11, PID-13, PV1-19 and PV1-44.
   Those are known gaps listed in `docs/hl7-fhir-mapping.md`, not incidents.
3. `reason="unparseable_date"` is the one to act on. Pull a trace id from the
   logs and look at the raw PID-7 in the dead-letter folder or the sender's
   output.

**Likely causes.** A sending system that has started including a time zone, a
precision the mapper does not handle, or a genuinely malformed date.

**Mitigation.** None is needed for safety: the record is written without the
field. Raise it with the sending trust, and add the format to
`hl7_to_fhir_date` if it is a legitimate one.

## 7. `InvalidNhsNumbers`

Link: `docs/runbook.md#7-invalidnhsnumbers`

**What it means.** An identifier arrived in PID-3 with an `NHS` assigning
authority and failed its Modulus 11 check digit. The NHS Data Dictionary says
that algorithm is mandatory, so a failure means the value is not an NHS
number.

The pipeline keeps the identifier on the resource, because the sender sent it,
but will not use it to look the patient up in PDS or to match an existing
record. Those writes become unconditional creates and show up in
`patient_write_total{identity="none"}`.

**Triage.**

1. One or two is a typo at the sending end.
2. A steady rate from one sender is a different numbering scheme in the NHS
   Number field. A Scottish CHI number is ten digits and will fail this check.
3. `sum by (service) (rate(nhs_number_invalid_total[1h]))` and cross-reference
   MSH-3 and MSH-4 in the logs to find which sender.

**Mitigation.** Do not switch the validation off. If a sender is supplying CHI
numbers, those need their own identifier system rather than being asserted as
NHS numbers.

## 8. `PatientWritesWithoutIdentity`

Link: `docs/runbook.md#8-patientwriteswithoutidentity`

**What it means.** More than a tenth of patient writes had no NHS number to
match on, so they were written as unconditional creates. A resend of any of
those produces a duplicate record.

**Why it is here.** This is the alert for a failure with no error. Every one of
those writes returned 200.

**Triage.**

1. Split by cause: `rate(nhs_number_invalid_total[30m])` tells you how much of
   it is failed check digits rather than absent numbers.
2. If the numbers are absent rather than invalid, the sender is not populating
   PID-3 with an NHS identifier at all.

**Known limitation.** This counts writes that had no identity to key on. It
does not detect the opposite problem, where the same person arrives under two
different NHS numbers and produces two records, which is what happens when a
patient moves between the Scottish and English NHS. Catching that needs a
matching step rather than a metric, and it is not built yet.

## 9. `DLQMessagesPastRetention`

Link: `docs/runbook.md#9-dlqmessagespastretention`

**What it means.** A message in the dead-letter path is older than the
retention window, which is 72 hours by default and set by `DLQ_RETENTION_HOURS`.

**Why it is here.** A dead-lettered message is the complete raw HL7 message,
every PID field, including the ones the mapper deliberately drops before it
builds the FHIR Bundle. That makes the dead-letter path the most sensitive
store in the stack, and until Part 5 it had no expiry at all while
`docs/security.md` claimed a storage-limitation control. This alert is here
because a retention rule that nothing measures is not a control, it is a
sentence in a document.

**Triage.**

1. `hl7_dlq_oldest_seconds` gives the age of the oldest surviving message.
2. If messages are old because nobody replayed them, replay them:
   `make reconcile` first to see what is missing, then resend from the queue.
3. If messages are old because pruning has not run, that is the known
   limitation below rather than a fault.

**Mitigation.** `docker compose restart transform` prunes on startup. In a
deployed environment the equivalent is the S3 lifecycle rule, which does not
depend on the service running at all.

**Known limitation.** Pruning runs when a message is written and when the
service starts. A stack that dead-letters nothing and never restarts keeps its
oldest message past the window, and this alert is what tells you so. The fix
is not a bigger prune, it is moving retention to the storage layer, where it
runs whether or not the application does.

## A note on these links

GitHub generates a heading's anchor from its text, so the link for each
section below is its number and name with the punctuation removed, for example
`#2-pdscircuitopen`. Every `runbook:` annotation in
`observability/prometheus/alerts.yml` points at one of them, and the alert rule
tests assert the exact string.

Until November 2026 those annotations pointed at short names such as
`#pds-circuit`, which were never anchors in this file and had never resolved.
An on-call engineer following an alert link landed at the top of the document.
Three of the eight were added in Part 4 by copying the convention without
checking that it worked.

The trade-off in the current scheme is that renumbering a section changes its
link. If sections are ever reordered, the annotations and the rule tests have
to move with them, and the rule tests are what will catch it.

## Generic playbook

For any alert that isn't listed above:

1. Acknowledge within 5 minutes to stop the page.
2. Write the symptom and what you've tried into the incident channel in real time — your future self and the post-mortem will thank you.
3. If patient safety is in doubt, escalate to the clinical safety officer immediately.
4. Prefer rollback over forward fix under fire. ArgoCD makes rollback a two-click operation.
5. After resolution: 30-minute cooldown, then write the post-mortem. Blameless. One line per contributing cause, one line per preventive action.
