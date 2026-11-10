# Security model

This document describes the threat model and the controls on this platform. It
replaces a version that mixed what is built with what would be built, in one
table, with no way to tell the two apart. That is a worse failure than having
no document, because an assessor reads a control table as a statement of fact.

Two rules now apply to every row below.

Each row says whether the control is implemented in this repository, and the
evidence column names a file that exists and, where it is a document, an
anchor that resolves. A diagram is not evidence. A pipeline that does not run
is not evidence.

Anything not implemented is in the "Claimed, not built" section rather than
in a control table with a qualifier attached to it.

## Data sensitivity

Every message is treated as patient-identifiable until proven otherwise.
`DataClass = patient-identifiable` is applied through `default_tags` on the AWS
provider, so it reaches every taggable resource in the configuration. Some
resources here take no tags argument at all, including `aws_kms_alias` and the
S3 bucket sub-resources, so the accurate claim is every taggable resource.

The dead-letter path is the most sensitive store in the stack and the least
obvious one. A dead-lettered message is the complete raw HL7 message, which
includes every PID field, including the fields the mapper deliberately drops
before building the FHIR Bundle. The FHIR store holds less patient data than
the dead-letter path does.

## Threats considered

| Threat | Control | Implemented | Evidence |
|---|---|---|---|
| Patient identifier leaking into logs | The NHS number never enters a log record, a metric label, or an outbound correlation header. The trace id is used instead. Enforced by tests that assert against the formatter's output, not the log record | Yes | `services/pds-client/tests/test_privacy.py` |
| Patient data outliving its purpose in the dead-letter path | 72 hour retention, pruned on write and on startup, with the age of the oldest message exported and alerted on | Yes, with the limits in `docs/runbook.md#9-dlqmessagespastretention` | `services/transform/main.py` (`_prune_dlq`), `services/transform/tests/test_dlq_retention.py`, `observability/prometheus/alerts.yml` |
| Unbounded request body at the one boundary that takes arbitrary input | 256 KiB limit enforced while reading the stream, not only from Content-Length | Yes | `services/ingest/main.py`, `services/ingest/tests/test_limits.py` |
| Container escape or privilege escalation | `runAsNonRoot`, `runAsUser: 10001`, `readOnlyRootFilesystem`, `allowPrivilegeEscalation: false`, all capabilities dropped, on all three services | Yes, in the Helm charts | `k8s/helm/{ingest,transform,pds-client}/values.yaml`, rendered in `docs/k8s-rendered/` |
| Lateral movement between pods | NetworkPolicy per service | Yes | `k8s/helm/{ingest,transform,pds-client}/templates/networkpolicy.yaml` |
| Secret leakage into Git | Gitleaks against the working tree and the full history on every push | Yes | `.github/workflows/ci.yml` (`secrets` job) |
| Code-level injection or unsafe deserialisation | Semgrep on every push | Yes | `.github/workflows/ci.yml` (`sast` job) |
| Image supply-chain compromise | Trivy image scan failing on HIGH or CRITICAL, Syft CycloneDX SBOM per image | Yes | `.github/workflows/ci.yml` (`image` job) |
| Infrastructure misconfiguration | Trivy config scan failing on HIGH or CRITICAL, plus `terraform validate` and `fmt -check` | Yes | `.github/workflows/ci.yml` (`iac-scan`, `terraform` jobs) |
| An alert rule that cannot fire | Alert rules unit tested with `promtool test rules` in CI, including a case asserting that a rule with no series exported is silent | Yes | `observability/prometheus/alerts_test.yml`, `.github/workflows/ci.yml` (`alerts` job) |
| Data tampering at rest, in the configuration that is written | KMS CMK encryption on RDS and S3, bucket versioning, Object Lock, public access blocked | Written in Terraform, never applied: see the note below | `infra/terraform/{kms,s3,rds}.tf` |
| Network-level snooping inside the VPC | VPC Flow Logs | Written in Terraform, never applied | `infra/terraform/vpc.tf` |
| Patient data leaving the UK | Region pinned to `eu-west-2` | Written in Terraform, never applied | `infra/terraform/variables.tf` |

### What "written in Terraform, never applied" means here

CI runs `terraform init -backend=false`, `validate` and `fmt -check`. It never
plans and never applies, and the provider is configured with fake credentials
on purpose. No AWS account is attached to this project. Those rows describe
configuration that is syntactically valid and reviewed, and that has never
created a resource. They are evidence of intent and of code review, not
evidence of an operating control, and an assessor should read them that way.

## The dead-letter path, which is where this document was most wrong

The previous version of this document cited the S3 lifecycle rule as the
storage-limitation control for patient data. That rule is real. It is in
`infra/terraform/s3.tf`, it transitions objects to STANDARD_IA at 30 days and
GLACIER at 90, and it expires them at 2555 days, which matches NHS records
retention. The bucket it governs is versioned, encrypted with a customer
managed key, has public access blocked and has Object Lock configured.

Nothing writes to it. No service in this repository imports boto3 or any other
S3 client. The transform service's own docstring says "S3 in prod", which was
an intention, not a description.

In every configuration that actually runs, the dead-letter path is local:

- Docker Compose: a named volume at `/tmp/dlq`. Until November 2026 the volume
  was root-owned while the container runs as uid 10001, so every dead-letter
  write failed with `PermissionError` and the message was lost. Fixed in
  `services/transform/Dockerfile`.
- Kubernetes: an `emptyDir` at `/tmp`. An `emptyDir` does not survive the pod.
  A restart or a reschedule during an outage loses exactly the messages the
  dead-letter path exists to preserve.

So the compliance posture before Part 5 was a well-built retention control
protecting an empty bucket, while the patient data sat in a volume with no
retention rule, no durability, and a runbook telling the on-call engineer to
"inspect the DLQ bucket" during an incident.

What is now true: the local path has a 72 hour retention window, pruning, an
exported age, an alert and a runbook entry. What is still not true: durability.
Moving the dead-letter path to the bucket that already has the right controls
needs an AWS account, and that is the next piece of work rather than something
this document should imply is done.

## Caldicott principles

| # | Principle | Position here |
|---|---|---|
| 1 | Justify the purpose | Every message is logged with its MSH-3 sending application and MSH-4 sending facility, and a trace id that follows it through all three services |
| 2 | Do not use personal confidential data unless necessary | Partly. The mapper persists only the fields it maps, and reports what it drops as `hl7_field_dropped_total`. The dead-letter path still holds the complete raw message, which is necessary for replay and is now time-bounded rather than indefinite. The earlier claim that ingress strips unused PID fields before persisting was not true: ingest forwards the message verbatim |
| 3 | Use the minimum necessary | The NHS number is no longer logged anywhere, and no patient identifier is used as a correlation id. Before Part 5 it appeared in four log lines and in the `X-Request-ID` header sent to the PDS sandbox |
| 4 | Access on a strict need-to-know basis | NetworkPolicy per service is implemented. IRSA and Kubernetes RBAC are written in Terraform and never applied. OPA policies were claimed and do not exist |
| 5 | Everyone with access understands their responsibilities | Not applicable to a personal project with one contributor. The earlier claim of an onboarding checklist and an annual information governance refresh described an organisation that does not exist |
| 6 | Comply with the law | UK GDPR position below |
| 7 | The duty to share can be as important as the duty to protect | PDS enrichment fails open: a PDS outage degrades data quality rather than dropping clinical messages. This is a deliberate trade-off and is recorded in `docs/runbook.md#2-pdscircuitopen` |
| 8 | Inform patients how their data is used | Out of scope. A transparency notice is the responsibility of a parent trust, and this platform has none |

## UK GDPR

| Principle | Position here |
|---|---|
| Lawful basis | Article 6(1)(e) public task with Article 9(2)(h) provision of healthcare would be the basis for a trust running this. This project processes synthetic data only and has no lawful basis to rely on, because it processes no personal data |
| Data minimisation | The FHIR store holds only mapped fields. The dead-letter path holds complete messages for up to 72 hours |
| Storage limitation | Implemented for the local dead-letter path at 72 hours, measurable as `hl7_dlq_oldest_seconds`. The S3 lifecycle rule is written and governs a bucket nothing writes to |
| Integrity and confidentiality | TLS terminates at the ALB in the Terraform configuration. Service-to-service traffic inside the cluster is plain HTTP. mTLS was claimed and is not built |
| Accountability | VPC Flow Logs are written in Terraform. CloudTrail and EKS audit logs were claimed and are not in the configuration at all |

## DSPT mapping

Offered as a worked example of how evidence is assembled, not as a submission.
A real assessment is made by an organisation about its own live systems, and
this is a personal project with no AWS account attached.

The table below is also dated in its shape. The toolkit's own guidance says
its content is organised under the ten National Data Guardian data security
standards, but the evidence items it now presents are Cyber Assessment
Framework style outcome codes such as B2.c and D1.a. A flat mapping to
standard numbers is therefore a reasonable way to think about evidence and not
a description of the current submission. Anyone doing this for real should
work from the toolkit itself at https://dsptoolkit.nhs.uk rather than from a
table in a repository.

| Standard | Evidence that exists |
|---|---|
| 1, personal confidential data | This document, and `services/pds-client/tests/test_privacy.py` as the enforcement |
| 4, managing data access | `k8s/helm/*/templates/networkpolicy.yaml`. IRSA in `infra/terraform/eks.tf` is written, not applied |
| 5, process reviews | `docs/runbook.md`, `docs/lessons-learnt.md` |
| 6, responding to incidents | `docs/runbook.md`, nine alerts each with a named triage section. Partly: the alerts fire but notify no one, see "Claimed, not built". The previous version of this row cited `docs/runbook.md#3-database-connection-pool-exhausted`, which has never existed |
| 7, continuity planning | Partly. RDS Multi-AZ and PITR are written in Terraform. Argo CD can re-sync. No RPO or RTO target has been measured, and the earlier claim of one was not based on a test |
| 8, unsupported systems | Pinned requirements, Trivy, Syft SBOM. Automated dependency update PRs were claimed; there is no Dependabot configuration |
| 9, IT protection | NetworkPolicy, container hardening, KMS CMK in Terraform. WAF was claimed, is not in the configuration, and the evidence given for it was a diagram |
| 10, accountable suppliers | Not applicable. There are no suppliers |

## Claimed, not built

These were in the previous version of this document as controls. They are
listed here so the control tables above contain only things that exist.

- Authentication on `POST /hl7`. There is none. Any caller that can reach the
  service can submit a message that becomes a Patient record. The mitigations
  that exist are that the subnets are tagged for an internal load balancer and
  that NetworkPolicy restricts pod-to-pod traffic. Neither is authentication.
  Tracked as a roadmap issue.
- Alert delivery. Earlier versions of this document did not claim it, but
  `docs/architecture.md` says "Alertmanager routes pages" and the runbook's
  prerequisites list an alerts Slack and PagerDuty queue. Neither exists.
  `observability/alertmanager/alertmanager.yml` routes to email through
  `mailhog`, but nothing loads it: Docker Compose runs no Alertmanager or
  mailhog service, `prometheus.yml` has no `alerting` block, and no Kubernetes
  manifest deploys one. The alert rules evaluate and show Firing in the
  Prometheus UI, and that is as far as they go. Raised by Nan, a reader of
  Part 4.
- mTLS between services, through a service mesh.
- Federated identity through NHS Care Identity Service 2.
- A local pre-commit hook for Gitleaks. Gitleaks runs in CI; there is no
  `.pre-commit-config.yaml`.
- Automated dependency update pull requests. There is no `.github/dependabot.yml`.
- AWS WAF with rate-based rules.
- CloudTrail and EKS control plane audit logs.
- Data masking in non-prod.
- Image signing with cosign.
- OPA policies.
- Any applied AWS infrastructure at all.

## Why the document was wrong, which matters more than the list

Every false claim in the previous version was written in the same voice as
every true one, in the present tense, in a table with an evidence column. The
format gave aspiration the appearance of fact, and the evidence column gave it
the appearance of verification. Four rows cited `.gitlab-ci.yml`, a pipeline
that could not run, because the repository is on GitHub. That file has been
deleted rather than left to be cited again.

The controls in this document are now checkable. Where a row names a test, the
test fails if the control is removed, which is the only form of security
documentation that stays true without someone remembering to update it.
