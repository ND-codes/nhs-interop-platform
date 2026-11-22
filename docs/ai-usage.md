# How AI was used on this project

This repository was built with Claude Code, used as an assistant by one
engineer. Every commit, merge and release here was made by a human who read
the diff first.

This document exists because the alternative is a reader guessing. A project
that mentions AI nowhere invites the assumption that none was used, and a
project that says "AI-assisted" and stops invites the assumption that nobody
checked. What follows is the specific version: what it did, what it got wrong,
and how each class of defect was actually found.

## What it was used for

- Reading the codebase against a question. "Which rows of `docs/security.md`
  are false" and "which steps of `QUICKSTART.md` fail on a machine that is not
  mine" were both answered this way, and both produced most of an article.
- Writing tests for defects already identified, including the discipline of
  running each new test against the previous commit to confirm it fails there.
- Drafting documentation, runbook sections and the blog articles, from notes
  and from the code.
- Verifying external claims against primary sources: HL7 v2.5.1, FHIR R4, the
  NHS Data Dictionary, Terraform's provider documentation, PEP 668,
  Alertmanager's configuration reference.

## What it was not used for

- Deciding what to build. The roadmap and the article order are the author's.
- Committing, merging, tagging or releasing.
- Any access to real patient data. The project uses the public PDS sandbox and
  synthetic messages only.

## Where the defects actually came from

Seven weeks, with every defect sorted by how it was discovered. This is the
part worth reading.

### Found by reading the code with a specific question

The commented-out Terraform `default_tags` block. Four NHS numbers in
`pds-client` log lines, and the same identifier used as an outbound
`X-Request-ID`. `cygpath` in the Makefile, which scoped `make test` to Git
Bash on Windows. `make integration` installing into a PEP 668 interpreter. A
`terraform.tfvars.example` the quickstart told you to copy and which did not
exist. Eight runbook links that had never resolved. Ten false claims in
`docs/security.md` and four more in `docs/architecture.md`. The absence of any
S3 client in a project whose compliance documentation cited an S3 lifecycle
rule as its retention control.

This is the strongest mode. It is also tireless in a way a person at the end
of a week is not.

### Found by running the system

The circuit breaker gauge that no service exported, found by starting
transform and reading `/metrics` rather than reading the code. A timing
prediction about `for: 2m` against a 30 second `reset_timeout` that turned out
to be wrong when measured. A fuzzing run over 28,000 mutated messages that
confirmed no patient value reached an exception message.

### Found by the author running the real thing

This is the longest list, and the most important one.

`@pds_breaker` applied to a coroutine function, so pybreaker counted the
returned coroutine object as a success and the breaker never opened. The fix
shipped in Part 4 for an alert that could never fire therefore still could not
fire. The test passed because it drove the breaker synchronously.

A dead-letter volume owned by root while the container runs as uid 10001, so
every dead-letter write failed with `PermissionError` and the message was
lost.

`hl7_dlq_oldest_seconds` set only when pruning ran, so it froze below its own
threshold and the alert built to watch it could never fire.

A raw `|` in a FHIR query, which RFC 3986 does not allow and Tomcat rejects
with a 400 that `curl -sf` then hid.

Two zlib CVEs that broke every image job until the base stage applied Debian
security updates.

`IngestNoTraffic` emailing a real person six times in two days about an idle
demo stack. No resolution email for any of it, because `send_resolved`
defaults to false for `email_configs`.

And one that belongs on its own. In Part 5 the assistant wrote eight tests
asserting that the NHS number never reaches stdout, and wrote them only for
`pds-client`, the service it happened to be editing. The author added
`services/transform/tests/test_pds_privacy.py`, which catches the identical
leak in `transform` through a different mechanism: `str(exc)` on an
`HTTPStatusError` embeds the request URL, and the URL contains the NHS number.
The tests had been shaped around the instance found rather than the property
claimed.

### Found by readers

Kevin Mayfield on CHI numbers arriving in the NHS Number field, and on the
number changing when a patient moves between the Scottish and English NHS,
which makes keying identity on it unsafe. Mustafa Erbay on PV1-19 for
encounter identity. Readers of Part 1 on conditional update against conditional
create, the PID-8 concept map, partial dates and PID-3 assigning authorities.
Nan on the alerts reaching nobody. Amorizz and upordown.online on the absence
of `absent()` and `up` alerts, which became Part 7. Kartik on `make break-pds`
not running in CI. Paul Lovelace on whether `make walkthrough` ran in CI,
which led to the `walkthrough` job.

### Found by the assistant's own tests before shipping

One. A pybreaker listener reading `new_state.state` where the attribute is
`new_state.name`. `getattr` with a default does not raise, so the gauge would
have sat at zero while the breaker was open: a fix that leaves the alert
unfireable and adds a green dashboard panel.

### Wrong because it was not read

Part 6 claimed `make restore-pds` left `PDSCircuitOpen` firing and needed to
send traffic. The script had sent three synthetic messages after the reset
timeout since Part 4. The assistant inferred its behaviour from a dashboard
instead of reading it, and the claim was published. A full run on 19 November
closed the breaker in under a minute, and Part 6 carries a correction.

### Caught by evaluating against a live stack

The first Part 7 draft added "and some traffic in the last 24 hours" to
`IngestNoTraffic`, to quiet an idle demo stack. It passed its promtool tests.
Evaluated against the running stack it still fired, because the demo had
carried traffic earlier that day, and it would have gone silent during any
outage longer than 24 hours. It was dropped before merging, and two promtool
cases now assert the cases it would have broken.

## The pattern

Every deep gap in this project was absence-shaped. A bucket nothing writes to.
An alert that reaches nobody. A metric no service exports. A retention rule
with no measurement. A resolution email that never sends. From inside the
repository they look exactly like everything being fine.

Reading found the ones a document pointed at. The security table cited a
lifecycle rule, so "what writes to this bucket?" could be asked and answered.
The alert annotations named runbook anchors, so each could be checked.

Reading did not find the ones nothing pointed at. No file said an Alertmanager
should be running, so nothing noticed there was none. Those were found by
running the system, or by a person asking a question the code could not
answer.

The practical consequence: write down what should exist, and reading has
something to check it against.

## What this means for the code

Three things in this repository exist because of that pattern, and they are
the parts most worth copying.

`make walkthrough` runs the documented tour as eight assertions and fails on
the first step that does not hold. The `walkthrough` CI job runs it on every
pull request against a stack started from nothing, so prose and behaviour
cannot drift apart silently.

`scripts/break-pds.sh` and `scripts/break-fhir.sh` drive real failures on
purpose, because an alert proved only by a unit test is not proved.

`ServiceDown`, `ServiceTargetMissing` and `BreakerMetricAbsent` in
`observability/prometheus/alerts.yml` alert on absence, including the literal
case of the gauge that `PDSCircuitOpen` depends on going missing again.

## Verification policy

Claims in this repository and in the articles are checked against primary
sources before publication, not recalled. That rule exists because of a
specific failure: a Terraform CI job shipped in Part 2 with an invalid trivy
flag, taken from memory rather than looked up, and the author had to fix it.

Where something could not be verified, the documentation says so rather than
hedging. `docs/security.md` keeps a "Claimed, not built" section for exactly
this reason.

## Scope and honesty

No part of the AWS configuration has ever been applied. `POST /hl7` has no
authentication. The dead-letter path does not survive a pod restart. There is
no Alertmanager in the Helm charts, so Kubernetes alert delivery does not
exist. These are listed in `docs/security.md` and in the article series rather
than implied away, and an assistant that is good at producing confident prose
is a reason to be stricter about that, not looser.
