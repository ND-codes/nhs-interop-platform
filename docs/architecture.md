# Architecture

![Architecture](architecture.svg)

## One-paragraph description

HL7 v2 messages from the trust integration engine land at an AWS ALB fronting the EKS cluster. The `ingest` service validates the MSH header and forwards the payload to `transform`, which parses the message, maps it to a FHIR R4 transaction Bundle, optionally enriches the Patient via `pds-client` (a thin wrapper around the NHS Spine PDS FHIR Sandbox), and persists the bundle to a HAPI FHIR server backed by RDS Postgres. Failed messages are dead-lettered to S3. Prometheus scrapes every service, Grafana renders the dashboards, Alertmanager routes pages. GitLab CI gates quality (lint, test, Trivy, Semgrep, Gitleaks, Syft SBOM) and pushes to an image registry; ArgoCD reconciles cluster state from Git.

## Core design principles

- **Fail open on enrichment, fail closed on writes.** PDS outages degrade data quality but never drop messages. FHIR write failures dead-letter for replay.
- **Stateless services, stateful store.** Every service is a Deployment that can be replaced at will; state lives in RDS, S3 and Secrets Manager.
- **Least privilege by default.** IRSA, not node IAM. NetworkPolicy deny-all with explicit allow-list. CMK-encrypted everything.
- **Observability is not optional.** Every request is traced, every service emits RED dashboards, every alert maps to a runbook entry.

## Non-functional targets

| Signal        | Target                                           | How we hit it                                                |
|---------------|--------------------------------------------------|--------------------------------------------------------------|
| Availability  | 99.95%                                           | Multi-AZ everything, minAvailable: 2 PDB, CrossZone ALB.     |
| RPO           | ≤5 minutes                                       | RDS PITR, S3 versioning, ArgoCD Git-backed state.            |
| RTO           | ≤15 minutes                                      | Automated failover at every tier.                            |
| MTTR          | 30% reduction vs baseline                        | Alerts wired direct to runbook sections with mitigation steps. |
| Throughput    | 200 msg/sec sustained, 1,000 msg/sec burst       | HPA on CPU + custom `hl7_queue_depth` metric (roadmap).      |

## Why this shape (and not something else)

- **Why not one monolith?** Ingest, transform and PDS have different failure modes and different scaling profiles. Splitting them lets us bulkhead around PDS without capping our ingress throughput.
- **Why HAPI FHIR, not HealthLake?** HAPI FHIR is open-source, production-proven at NHS trusts, and avoids regional availability caveats. HealthLake is compelling for analytics overlay but was not a hard requirement for this scope.
- **Why in-memory queue, not SQS from day one?** Keeps the local demo one-command. SQS FIFO is on the roadmap and is a drop-in swap at the ingest→transform boundary.
- **Why EKS, not ECS Fargate?** The JD emphasises Kubernetes, and the team's existing tooling (ArgoCD, Helm, Prometheus) assumes Kubernetes.
