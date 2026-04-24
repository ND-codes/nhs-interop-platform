# Security model

This document captures the threat model, the controls in place, and the evidence that ties each control to a standard (DSPT, Caldicott, NHS Digital requirements).

## Data sensitivity

Every byte on this platform is treated as patient-identifiable information (PII) until proven otherwise. Data classification tag `DataClass=patient-identifiable` is applied to every AWS resource via Terraform default tags.

## Threats considered

| Threat                                | Control                                                                                               | Evidence                       |
|---------------------------------------|-------------------------------------------------------------------------------------------------------|--------------------------------|
| PII exfiltration via compromised pod  | NetworkPolicy deny-all + allow-list; IRSA-scoped IAM; read-only root filesystem; no long-lived keys   | `k8s/helm/*/templates/networkpolicy.yaml` |
| Credential theft                      | Secrets Manager + KMS CMK + IRSA; no secrets in env files in Git                                      | `infra/terraform/rds.tf`, `.gitignore` |
| Image supply-chain compromise         | Trivy scan with HIGH/CRITICAL exit; Syft SBOM per build; signed images (cosign in prod roadmap)       | `.gitlab-ci.yml`               |
| Code-level injection / unsafe deserialisation | Semgrep SAST on every MR; Pydantic input validation at every HTTP boundary                   | `.gitlab-ci.yml`, `services/*/main.py` |
| Secret leakage into Git               | Gitleaks on every push; pre-commit hook locally                                                       | `.gitlab-ci.yml`               |
| Man-in-the-middle                     | TLS 1.2+ only on ALB; mTLS for service-to-service via service mesh sidecar (prod); Spine Secure Proxy for PDS | `infra/terraform/eks.tf`, `k8s/helm/*/` |
| Data tampering at rest                | KMS CMK envelope encryption on RDS, S3, EKS secrets, EBS; Object Lock on audit bucket (prod)          | `infra/terraform/{kms,s3}.tf`  |
| Insider snooping                      | CloudTrail + VPC Flow Logs + EKS audit logs; least-privilege IAM via IRSA                             | `infra/terraform/{vpc,eks}.tf` |
| DDoS / abuse                          | AWS WAF with rate-based rules; ALB CrossZone; Horizontal Pod Autoscaler absorbs bursts                 | `docs/architecture.svg`        |
| Dependency compromise                 | Dependabot-style auto-PRs; locked pins; SBOM published per release                                    | `requirements.txt`             |

## Caldicott principles — how we satisfy each

1. **Justify the purpose.** Every message is logged with the originating MSH-3/MSH-4 pair.
2. **Only use PID when necessary.** Ingress strips unused PID fields before persist.
3. **Minimum necessary PID.** Patient resource only persists the fields FHIR requires.
4. **Access on a strict need-to-know basis.** IRSA + Kubernetes RBAC + OPA policies.
5. **Everyone with access is aware of their responsibilities.** Onboarding checklist + annual IG refresh, plus audit-log reviews.
6. **Comply with the law.** UK GDPR + DPA 2018 flows documented below.
7. **The duty to share can be as important as the duty to protect.** PDS fails open rather than dropping messages.
8. **Inform patients about how their data is used.** Transparency notice is the responsibility of the parent trust, but this platform exposes the audit log evidence they need.

## UK GDPR flows

| Principle              | How this platform honours it                                                         |
|------------------------|---------------------------------------------------------------------------------------|
| Lawful basis           | Article 6(1)(e) public task + Article 9(2)(h) provision of healthcare.               |
| Data minimisation      | Only mapped HL7 fields are persisted; everything else is dropped at transform time.   |
| Storage limitation     | S3 lifecycle: hot 30 days, cool 60 days, Glacier 90 days, expire at 7 years.          |
| Integrity and confidentiality | KMS-CMK encryption at rest, TLS 1.2+ in transit, mTLS service-to-service.     |
| Accountability         | CloudTrail, VPC Flow Logs, EKS audit logs, all with 90-day retention minimum.         |

## DSPT mapping — what to show an auditor

| DSPT standard                                  | Evidence                                          |
|------------------------------------------------|---------------------------------------------------|
| 1 — Personal confidential data                 | this document                                     |
| 4 — Managing data access                       | `infra/terraform/eks.tf` (IRSA), RBAC manifests   |
| 5 — Process reviews                            | `docs/runbook.md`                                 |
| 6 — Responding to incidents                    | `docs/runbook.md#3-database-connection-pool-exhausted` and Alertmanager routing |
| 7 — Continuity planning                        | RDS Multi-AZ + PITR, ArgoCD can re-sync in <1 min, RPO/RTO targets |
| 8 — Unsupported systems                        | Dependabot pins, Trivy, Syft SBOM                 |
| 9 — IT protection                              | WAF, NetworkPolicy, mTLS, KMS CMK                 |
| 10 — Accountable suppliers                     | Supplier assurance — SBOMs, SLA, pen-test evidence, exit plan |

## What's deliberately out of scope for the demo

- Real mTLS between services — the prod mesh (Istio / Linkerd) is one of the roadmap items.
- Federated identity (NHS Care Identity Service 2) — documented but not wired.
- Data masking in non-prod — noted, not implemented.
