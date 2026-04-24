# Runbook

One entry per alert. Each entry has: what fires, what it means, what to check, what to do, what to write up afterwards. If you're reading this at 3am, start with the "Immediate actions" section.

## On-call prerequisites

- AWS SSO access to the target account, role `interop-oncall`.
- `kubectl` context set to the target cluster (`aws eks update-kubeconfig --name nhs-interop-<env>-eks --region eu-west-2`).
- Access to Grafana (`https://grafana.<env>.nhs-interop.example`), Prometheus, and the alerts Slack/PagerDuty queue.

---

## 1. `TransformLatencyHigh`

**What fires.** p95 latency on the transform service >1s for 10 minutes.

**What it usually means.** Either PDS is slow (most common), the transform pod is CPU-saturated, or the FHIR server is backpressuring.

**Immediate actions.**
1. Grafana → HL7 Pipeline → PDS lookup latency. If high, the problem is PDS — skip to §2.
2. Grafana → HL7 Pipeline → Requests per second. If traffic has doubled, check the HPA: `kubectl -n interop get hpa`. If it has hit max, scale the cap or add a node group.
3. Grafana → FHIR writes. If failure rate is non-zero, the FHIR backend (RDS) is the bottleneck — check RDS CloudWatch panels for CPU, connection count, and replica lag.

**Mitigation.**
- Temporary scale: `kubectl -n interop scale deploy transform --replicas=<n>`. HPA will fight you — suspend it first with `kubectl autoscale ... --min=<n>`.
- If FHIR writes are failing, inspect the DLQ bucket — messages are queued there and will replay once the write path recovers.

**Post-incident.** Add the query pattern to the load model. If HPA cap was hit, raise it. If RDS CPU was the cause, schedule an instance-class bump.

---

## 2. `PDSCircuitOpen`

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

**What fires.** More than 6 HL7 messages per minute are being dead-lettered.

**What it usually means.** A new upstream has started sending malformed messages, or we have a mapping bug that choked on a field pattern we haven't seen before.

**Immediate actions.**
1. `aws s3 ls s3://nhs-interop-<env>-dlq/ --recursive | tail -20` — fetch a couple of recent messages.
2. Inspect the transform logs for the trace id: `kubectl -n interop logs deploy/transform --since=15m | grep <trace_id>`.
3. Classify: is it an upstream bug (wrong field order, unknown segment) or our bug (unhandled edge case in the mapper)?

**Mitigation.**
- Upstream bug: raise a ticket with the sending trust; the DLQ preserves the message for replay once fixed.
- Our bug: write a failing unit test against the fixture, patch the mapper, redeploy. Replay the DLQ batch.

**Post-incident.** Every mapping bug gets a regression test. The fixture goes into `tests/fixtures/hl7/`.

---

## 4. `TransformErrorRateHigh`

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

**What fires.** Zero HL7 messages received for 15 minutes.

**What it usually means.** Either the upstream trust integration engine is down (most common — not our problem to fix, but it's our problem to notice), the ALB has a listener misconfiguration, or our ingress NetworkPolicy is too tight after a recent change.

**Immediate actions.**
1. `curl` the ALB from outside the VPC — if it 503s, it's infra.
2. Check the sending trust's integration team Slack / email.
3. `kubectl -n interop describe networkpolicy` — did anything change in the last 24h?

**Mitigation.** If it's the trust, acknowledge the alert and wait. If it's us, revert the NetworkPolicy change.

---

## Generic playbook

For any alert that isn't listed above:

1. Acknowledge within 5 minutes to stop the page.
2. Write the symptom and what you've tried into the incident channel in real time — your future self and the post-mortem will thank you.
3. If patient safety is in doubt, escalate to the clinical safety officer immediately.
4. Prefer rollback over forward fix under fire. ArgoCD makes rollback a two-click operation.
5. After resolution: 30-minute cooldown, then write the post-mortem. Blameless. One line per contributing cause, one line per preventive action.
