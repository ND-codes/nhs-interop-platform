---
title: "Part 6: The Walkthrough That Only Worked on My Machine"
published: false
tags: devops, healthcare, docker, career
series: Building an NHS Interop Platform
---

This is Part 6 of a series building an HL7 v2 to FHIR R4 interoperability
service in public. The repository is
[ND-codes/nhs-interop-platform](https://github.com/ND-codes/nhs-interop-platform),
and the code as it stood for this article is tagged `part-6`.

The plan for this week was a ten-minute tour of the repository and the
questions an interviewer asks about it. Before writing the tour, I did the
obvious thing and followed the one I already had.

It does not work on a machine that is not mine. Not in a subtle way.

## A walkthrough is a promise

Most of what a repository claims is hard to check. A threat model, an
architecture diagram, a note about scaling: a reader takes those on trust or
not at all.

A quickstart is different. It is a sequence of commands with stated outcomes,
and the reader finds out within minutes whether you were telling the truth. It
is the most falsifiable document in the repository and usually the least
tested, because the author is the one person who can never run it fresh.

Mine had four failures. Three of them break on the reader's machine and work
perfectly on mine. The fourth was not about machines at all.

## `make test` could not run outside Git Bash

Step 7 of my quickstart said `make test`. The recipe:

```make
docker run --rm -v "$$(cygpath -m $$(cd services/$$svc && pwd))":/app -w //app python:3.12-slim \
  sh -c "pip install -q -r requirements-dev.txt && pytest -q"
```

`cygpath` converts a Windows path into the `C:/` form Docker wants. It exists
in Git Bash on Windows and nowhere else. On macOS or Linux that line is
`cygpath: command not found`, and `make test` and `make lint` both die on the
first service.

I work in Git Bash on Windows. It had never once failed for me, which is the
entire problem.

The fix keeps the Windows behaviour exactly and adds the other path:

```sh
abs=$(cd services/$svc && pwd) ; wd=/app ;
if command -v cygpath >/dev/null 2>&1 ; then abs=$(cygpath -m "$abs") ; wd=//app ; fi ;
docker run --rm -v "$abs":/app -w "$wd" python:3.12-slim ...
```

The doubled `//app` is the other half of the same problem. MSYS rewrites a
leading `/app` into a Windows path, so Git Bash needs it doubled and nothing
else does. Both branches were tested, the second by putting a fake `cygpath`
on `PATH`. Then the Git Bash branch ran for real on my machine: 92 tests, all
passing.

## `make integration` installed into the system interpreter

```make
python3 -m pip install --quiet httpx pytest
```

On Debian, Ubuntu and Homebrew Python 3.11 and later, that is refused outright.
PEP 668 lets a distribution mark its base interpreter as externally managed,
and pip then declines with `error: externally-managed-environment`. It works on
my machine because my Python is not marked that way.

It builds a throwaway virtualenv now, with a `bin` and `Scripts` fallback so
the same line works in Git Bash.

## A file the quickstart told you to copy did not exist

```bash
cp terraform.tfvars.example terraform.tfvars
```

There was no `terraform.tfvars.example`. There never had been. You only find
that by following your own instructions as though you had not written them.

## The alert that reached nobody

The fourth is the one worth the article, and I did not find it. Nan, a reader
of Part 4, did.

Part 4 was about an alert that could never fire, because it watched a metric
no service exported. I fixed that, exported the gauge, wrote the rule unit
tests, added the runbook sections, and built scripts to open the circuit
breaker on purpose so you could watch `PDSCircuitOpen` go Firing.

Nan's question was roughly: and then who gets told?

Nobody. Here is the whole chain:

- `observability/alertmanager/alertmanager.yml` exists, and is a reasonable
  config. It routes by severity, groups by alertname and service, and has an
  inhibit rule.
- Nothing starts an Alertmanager. There is no such service in Docker Compose
  and no such manifest anywhere in `k8s/`.
- `prometheus.yml` had no `alerting` block, so even if one had been running,
  Prometheus did not know where it was.
- That config's `smtp_smarthost` pointed at `mailhog:1025`. No file in the
  repository has ever started a mailhog.

So the rules evaluated, turned red in the Prometheus UI, and stopped there.
Meanwhile `docs/architecture.md` said "Alertmanager routes pages" and the
runbook's prerequisites told the on-call engineer they would need access to
"the alerts Slack/PagerDuty queue".

Part 4 made the alert fireable. Part 5 made the documentation honest about the
controls. Neither of us noticed that the last hop did not exist, and it is the
hop that makes the other two matter. An alert nobody receives is a dashboard.

The fix is small, which is the irritating part:

```yaml
alerting:
  alertmanagers:
    - static_configs:
        - targets: ['alertmanager:9093']
```

Plus two services in Compose. Alertmanager, and Mailpit as the destination:
a disposable SMTP sink with a web inbox, which holds mail in memory and
forwards it nowhere. That is what makes it safe to point a demo at. I used
Mailpit rather than the MailHog the old config named, because MailHog has not
been released since 2020 and Part 5 was about not shipping things you would
not defend.

Now `make break-pds` opens the breaker, `PDSCircuitOpen` goes Firing about two
minutes later, and the notification arrives at a web inbox on port 8025 where
you can read it. The whole path, end to end, in a demo you can run on a laptop.

![Prometheus alerts page showing PDSCircuitOpen in the Firing state, with the rule expression pybreaker_state equals 1 for 2 minutes, its runbook link and summary](UPLOAD-part-6-pds-firing.png)

![Mailpit inbox with four alert emails, two PDSCircuitOpen and two IngestNoTraffic, and the newest PDSCircuitOpen email open showing its labels, runbook link and summary](UPLOAD-part-6-alert-email.png)

I ran it on 17 November, and again this morning for the screenshots. The
subject line began `[FIRING:1] PDSCircuitOpen transform`. Six parts in, it is
the first alert this project has ever delivered to anything.

The inbox also holds two `IngestNoTraffic` alerts I did not ask for. I checked
the rule in `alerts.yml` before calling it a fault: it fires once ingest has
gone 15 minutes without an HL7 message, and the stack sat idle overnight. So it
is expected, but a demo stack left running will email you about its own quiet.

## Making the tour testable

The underlying failure is that a quickstart is prose, and prose does not run in
CI. So the tour is now a script, and `make walkthrough` runs it.

Eight checks. The stack is up, ingest accepts an ADT, the Patient reaches FHIR
keyed on the NHS number, a lab result produces Observations, Prometheus is
scraping every service, Prometheus has discovered an Alertmanager, Alertmanager
is healthy, and the inbox is reachable. Each one fails loudly and stops.

The sixth is the one that would have caught Nan's finding:

```bash
discovered=$(curl -sf "$PROM/api/v1/alertmanagers" \
  | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["data"]["activeAlertmanagers"]))')
[ "$discovered" -ge 1 ] || fail "Prometheus has no active Alertmanager."
```

Two small things I would do again. The FHIR checks parse the Bundle rather than
grepping for `"total"`, because the whitespace is not guaranteed and a grep for
that string also matches a zero. And the Mailpit check asserts reachability but
reads the message count defensively, because I could not get Mailpit's API
documentation to load and I was not willing to assert a field name I had not
seen. A schema change should not turn a working inbox into a failed tour.

## The tour failed its own first run

The script was written and checked without a Docker daemon. The first time it
ran against a real stack, it stopped at check 3:

```
[3/8] The Patient reaches FHIR, keyed on the NHS number
      FAIL the Patient did not become true within 30s
```

The Patient was there. Asking HAPI for a count returned one. The query was the
problem. A FHIR token search separates system and code with a pipe,
`identifier=https://fhir.nhs.uk/Id/nhs-number|9000000009`, and RFC 3986 does
not allow an unencoded `|` in a query string. HAPI runs on Tomcat, which
enforces that by default and answers HTTP 400.

The 400 never reached the screen. `curl -sf` turns any HTTP error into a
silent non-zero exit, and the retry loop read that as "not there yet". Thirty
seconds later it reported a missing Patient that was never missing.

The fix is `%7C` in place of the pipe, in the script and in the quickstart. The
quickstart needed it too: Tomcat's own documentation says the major browsers
send that character unencoded, so the link would have failed in a browser as
well.

This is the alert story again, at a smaller scale. A check that cannot tell
"not found" from "you asked the wrong way" will pass or fail for the wrong
reason, and you only find out by running it.

![Terminal output of make walkthrough with all eight checks reporting ok, including check 3 finding the Patient at a URL with the pipe encoded as %7C](UPLOAD-part-6-walkthrough-run.png)

## Four things the architecture document still claimed

While I was at it. `docs/architecture.md` said messages are dead-lettered to
S3, which Part 5 established is false. It said GitLab CI gates quality, two
weeks after we deleted `.gitlab-ci.yml`. It said CI pushes to an image
registry, which it does not, because there is nowhere to push to. And it said
"every request is traced", when what actually exists is a trace id carried in
log lines. That is correlation, not distributed tracing, and there is no
OpenTelemetry in any of these services.

All four are corrected in place, with a short note saying what each one used to
claim. Quietly editing them would have been tidier and worse.

## The questions an interviewer actually asks

This is the other half of what Part 6 was meant to cover, and the walkthrough
work made it easier rather than harder, because the questions people ask about
a system are mostly the ones a broken walkthrough exposes.

### Walk me through what happens to a message.

It arrives at `POST /hl7` on
ingest, which validates the MSH segment and assigns a trace id. Transform
parses it, maps it to a FHIR R4 transaction Bundle, optionally enriches the
Patient from PDS, and writes the Bundle to HAPI FHIR with a conditional update
keyed on the NHS number. Failures dead-letter to a local path. The trace id
follows the message through all three services.

### What happens when PDS is down?

Enrichment fails open. A circuit breaker
opens after five consecutive failures and the pipeline keeps writing Patients
with locally derived demographics. Data quality degrades; no clinical message
is dropped. That is a deliberate trade-off and it is the seventh Caldicott
principle in practice, the one that says the duty to share can matter as much
as the duty to protect. You can watch it with `make break-pds`.

### Who gets paged?

Until this week, nobody, and I would say exactly that. The
rules evaluated and notified no one. It is fixed for the local stack and still
missing in Kubernetes. A question like this is a gift, because the honest
answer is more interesting than the tidy one.

### How do you know the mapping is correct?

Unit tests per defect, each written
to fail against the previous code. Part 3 covers nine specific ways an HL7 v2
to FHIR mapping goes wrong, most of them found by readers. The honest limit is
that there is no conformance suite and no validation against the HL7 v2 to FHIR
implementation guide; the tests encode the defects we know about.

### Where does patient data live and for how long?

Mapped fields in HAPI FHIR
on Postgres. Complete raw messages in the dead-letter path, which holds more
patient data than the FHIR store does, for up to 72 hours. That retention is
new as of Part 5 and the path still does not survive a pod restart.

### What is the worst bug you have shipped here?

Part 4's fix for the alert
that could never fire. The circuit breaker was applied as a decorator to a
coroutine function, so it counted the returned coroutine object as a success
and never opened, which means the gauge I had just added and the alert I had
just made fireable still could not fire. The test passed because it drove the
breaker synchronously. I found it by running the stack.

### How would you scale it?

The ingest to transform hop is synchronous HTTP,
which keeps the local demo one command and is the first thing I would replace,
with an SQS FIFO queue using the NHS number as the message group id so a
patient's messages stay ordered. Beyond that the services are stateless and
scale horizontally; the bottleneck is the FHIR write path.

### What would you do differently?

Written the documentation last. Almost every
false claim in this repository was true of the design at the moment it was
written and never revisited.

## What I did not fix

There is still no Alertmanager in the Helm charts, so the Kubernetes path
notifies nobody. I did not write one, because I cannot deploy a cluster to test
it and shipping untested YAML two days before publishing is the exact thing
Part 5 was about. It is in the "Claimed, not built" section of
`docs/security.md` and a roadmap issue.

There is no paging integration anywhere. The Alertmanager config has a
PagerDuty receiver commented out and has never been pointed at a routing key.

`make restore-pds` brings PDS back but does not clear the alert. The breaker
only tries the call again when the next request arrives, so with no traffic it
stays open and `PDSCircuitOpen` keeps firing after the outage is over. One
synthetic message closed it. The restore script should send that message, and
the runbook should say so. That is next week's pull request.

![Grafana PDS circuit breaker panel holding at 1, open, for about seven minutes before dropping back to 0](UPLOAD-part-6-breaker-panel.png)

The retry loop still hides the status code. The pipe is fixed, but the next
malformed query would fail the same silent way.

And I have not timed the tour on a second machine. The series plan called this
part a ten-minute walkthrough; the document now says the figure should be
yours rather than mine.

## Worth stealing

Run your own quickstart on a machine you did not configure. A colleague's, a
fresh VM, a container. The author is the one person who cannot test it.

Grep your build tooling for anything platform-specific. `cygpath`, `open`,
`sed -i` with a bare `-i`, `realpath`, `date -d`. Each one quietly scopes your
repository to one operating system.

Make the tour a target. Prose cannot run in CI; a script can, and then the
document and the repository cannot drift apart silently.

Check the last hop of anything that is supposed to tell a human. Working alert
rules that notify nobody look identical to working alerting from inside the
repository.

Correct a stale claim in place, with a line saying what it used to say. The
edit history is not where a reader looks.

## Where AI helps here, and where it doesn't

I build this with Claude Code, and this week drew the line unusually clearly.

Reading the Makefile and noticing that `cygpath` is Git Bash only is exactly
the kind of thing it is good at. So is knowing that PEP 668 would refuse the
pip line, and checking `terraform.tfvars.example` against the variables that
are actually declared. That sweep took minutes and I would not have done it by
hand two days before publishing.

But none of that is the same as running the thing. It wrote and checked this
week's changes in a sandbox with no Docker daemon, so "works on my machine"
problems were invisible to it by construction. It found the `cygpath` problem
by reading, after I pointed it at the walkthrough, which is not the same as the
problem announcing itself.

The walkthrough script is the clearest example. It was reviewed, shellchecked
and clean, and it failed its first real run on a URL encoding rule. Once the
stack was up on my machine, the same assistant ran the query directly, found
the 400 in about a minute, and checked the fix against the RFC and Tomcat's
documentation. Reading wrote the bug. Running found it.

The alert delivery gap is the sharper case. It was not found by reading at all,
by me or by a model. Nan found it by asking the one question neither of us had:
and then who gets told? Two articles in a row went past it while looking
directly at the alerting configuration.

The pattern I would now state plainly, three weeks running: reading finds
inconsistencies, running finds defects, and other people find the questions.

## Sources

- [PEP 668](https://peps.python.org/pep-0668/), on externally managed Python environments
- [Prometheus configuration](https://prometheus.io/docs/prometheus/latest/configuration/configuration/), for the top-level `alerting` block
- [Prometheus HTTP API](https://prometheus.io/docs/prometheus/latest/querying/api/), for the `/api/v1/alertmanagers` response shape
- [RFC 3986, section 3.4](https://www.rfc-editor.org/rfc/rfc3986#section-3.4), the characters allowed in a URI query
- [Apache Tomcat HTTP connector](https://tomcat.apache.org/tomcat-10.1-doc/config/http.html), `relaxedQueryChars` and the characters rejected by default
- [Mailpit, Docker install](https://mailpit.axllent.org/docs/install/docker/)
- [The Caldicott Principles](https://www.gov.uk/government/publications/the-caldicott-principles), National Data Guardian
- Alertmanager and Prometheus configurations in this article were validated with `amtool check-config` 0.34.1 and `promtool check config`

## Next

Part 7 is the last one: where AI fits in this work and where it does not, with
seven weeks of specifics rather than opinions.

If you have a quickstart in a repository you maintain, run it somewhere else
this week. What broke first?

---

This is a personal project. It is not affiliated with or endorsed by NHS England or any NHS organisation. It uses the public PDS FHIR sandbox and synthetic test data only. Views are my own.
