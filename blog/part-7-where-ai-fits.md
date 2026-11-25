---
title: "Part 7: What Reading the Code Could Never Find"
published: false
tags: ai, devops, healthcare, testing
series: Building an NHS Interop Platform
---

This is the last part of a series building an HL7 v2 to FHIR R4
interoperability service in public. The repository is
[ND-codes/nhs-interop-platform](https://github.com/ND-codes/nhs-interop-platform),
and the code as it stood for this article is tagged `part-7`.

If you are new here, the short version. Many hospital systems send patient
events as HL7 version 2, an older plain-text format. National NHS services are
moving to FHIR, a newer web format. Something in between has to translate each
message, check the patient against national records, and tell someone when it
breaks. This repository is a small, working version of that layer. It runs on
a laptop with synthetic test data, and you can fork it and follow along.

I built this with Claude Code, and said so in Parts 4, 5 and 6. This part is
the accounting.

Most writing about AI and code argues from impressions. I have seven weeks of
dated evidence instead: every defect in this project, and how each one was
actually found. The answer turned out to be more specific than "it helps with
boilerplate" and more useful than "always review the output".

## The accounting

Sorting every defect by discovery route.

### Found by reading the code against a specific question

A Terraform `default_tags` block that was commented out while the compliance
documentation described its benefit in the present tense. Four NHS numbers in
`pds-client` log lines, plus the same identifier used as an outbound
`X-Request-ID`. A `cygpath` call that scoped `make test` to Git Bash on
Windows. `make integration` installing into a PEP 668 interpreter. A
`terraform.tfvars.example` the quickstart told you to copy and which did not
exist. Eight runbook links that had never resolved. Ten false claims in
`docs/security.md` and four more in `docs/architecture.md`. The complete
absence of any S3 client in a project whose compliance table cited an S3
lifecycle rule as its retention control.

That is a long list and it is the strongest mode. Reading forty files and
checking whether each cited thing exists is tedious, mechanical, and exactly
the work that gets skipped at the end of a week. Most of Parts 5 and 6 came
from one question each: which rows of this document are false, and which steps
of this quickstart fail on a machine that is not mine.

### Found by running the system

The circuit breaker gauge that no service exported, found by starting
transform and reading `/metrics` instead of reading the code. A timing claim
about `for: 2m` against a 30 second `reset_timeout` that turned out to be
wrong when measured. A fuzzing run over 28,000 mutated messages confirming no
patient value reached an exception message.

### Found by me running the real thing

The longest list, and the one that matters.

The PDS circuit breaker was applied as a decorator to a coroutine function.
pybreaker counted the returned coroutine object as a success, the real failure
raised later on await, outside the breaker, and the breaker never opened. The
fix Part 4 shipped for an alert that could never fire therefore still could
not fire. The test passed because it drove the breaker synchronously.

A dead-letter volume owned by root while the container runs as uid 10001, so
every dead-letter write failed with `PermissionError` and the message was
lost.

`hl7_dlq_oldest_seconds` set only when pruning ran, so it froze below its own
threshold and the alert built to watch it could never fire.

A raw `|` in a FHIR query, which RFC 3986 does not permit in a query and
Tomcat rejects with a 400 that `curl -sf` then swallowed, so the walkthrough
reported a missing Patient that was there.

Two zlib CVEs that broke every image job until the base stage applied Debian
security updates.

And then alerts actually reached a person. `IngestNoTraffic` emailed me six
times in two days about a demo stack nobody was using, which is the rule doing
its job on a stack left idle. None of the alerts ever
told me they were over, because `send_resolved` defaults to false for
`email_configs`, so from an inbox an incident that has ended looks exactly like
one still running.

### Found by readers

Kevin Mayfield on CHI numbers arriving in the NHS Number field, and on the
number changing when a patient moves between the Scottish and English NHS,
which makes keying patient identity on it unsafe. Mustafa Erbay on PV1-19 for
encounter identity. Readers of Part 1 on conditional update against
conditional create, the PID-8 concept map, partial birth dates, PID-3
assigning authorities. Nan on the alerts reaching nobody. Amorizz and
upordown.online on the absence of `absent()` and `up` alerts, which became
most of this week's code. Kartik on `make break-pds` not running in CI. Paul
Lovelace on whether `make walkthrough` ran in CI. It did not. It does now.

### Found by the assistant's own tests, before shipping

One. A pybreaker listener reading `new_state.state` where the attribute is
`new_state.name`. `getattr` with a default does not raise, so the gauge would
have sat at zero while the breaker was open: a fix leaving the alert
unfireable while adding a green dashboard panel.

One, out of all of them.

### A mistake in the other direction

Part 6 said `make restore-pds` left the alert firing and the script should be
fixed to send traffic. It already sent traffic, and had since Part 4. The
assistant described the script from what the dashboard showed, without reading
it, and I published that. Running the full script again this week closed the
breaker in under a minute. Part 6 now carries a correction.

And one that nearly shipped this week. The first draft of this part's code
quieted those overnight emails by only alerting if traffic had arrived in the
last 24 hours. It read well and passed its tests. Evaluated against my live
stack while it sat idle, it fired anyway, because the demo had carried traffic
earlier that day. Worse, it would have gone silent once an outage passed a
day.
It was dropped, and two tests now hold that line.

So the lesson is not "running beats reading". Each catches what the other
misses, and skipping either one is how a wrong claim gets out.

## The one that belongs on its own

In Part 5 I had the assistant fix a patient identifier leak. It wrote eight
tests asserting the NHS number never reaches stdout, deliberately asserting
against the formatter's serialised output rather than the log record, because
the defect lived in serialisation and `caplog` would have passed.

Good tests. They covered `pds-client`, the service being edited.

The identical leak existed in `transform`, through a completely different
mechanism. `transform` logs `str(exc)` when a PDS lookup fails, and an
`HTTPStatusError` builds its message from the request URL, and the URL is
`PDS_CLIENT_URL/<nhs_number>`. Every failed lookup logged the patient
identifier.

I found it by running a real PDS failure rather than the connection error the
tests used, whose message contains no URL. The fix is
`services/transform/tests/test_pds_privacy.py`.

The tests had been shaped around the instance that was found, not the property
that was claimed. The claim was "the NHS number never reaches our logs". What
was tested was "these four call sites no longer pass it in `extra`". Those
look the same from inside the diff and are not the same thing at all.

## The pattern

Look again at the deepest problems in this project. An S3 bucket with perfect
retention controls that nothing writes to. An alert rule watching a metric no
service exports. A runbook link pointing at an anchor that was never created.
An Alertmanager configuration with no Alertmanager. A retention window with no
measurement. A resolution email that never sends.

Every one of them is absence-shaped. And from inside the repository, absence
looks exactly like everything being fine. There is no red line in a diff for a
file that does not exist, no failing test for a notification nobody receives,
no warning for a rule whose series was never created. The code reads correctly
because the code is correct. What is missing is the part that would have made
it matter.

Reading did find some of them, and the way it found them is the useful part.
The empty bucket came to light because the security table cited a lifecycle
rule, so there was something to check: what writes to this bucket? The dead
runbook links came to light because each alert named an anchor, so there was
something to check: does it resolve? Reading finds a missing thing when a
document points at where it should be.

When nothing written down names the missing thing, reading has nothing to
check against. No file said "an Alertmanager runs here", so no amount of
reading noticed there was not one. That was found by a person asking who gets
told. The unexported gauge was found by starting the service and looking.

An assistant reads. It reads very well and very patiently, and it has no inbox
and no on-call rotation. It notices a missing thing when a document points at
it. It cannot notice that nothing arrived.

That is not a limitation you fix with a better prompt. It is a consequence of
where the thing sits. It does suggest a cheap habit, though: write down what
should exist, so reading has something to check.

## What that changed in the code

Three parts of this repository exist because of that pattern, and they are the
parts most worth copying.

`make walkthrough` runs the documented tour as eight assertions and stops on
the first step that does not hold. Since this week it runs in CI on every pull
request, starting all nine services from nothing, so the quickstart and the
behaviour cannot drift apart quietly. A failed check now says which HTTP status
it got, so the Part 6 bug would have reported itself in two seconds rather
than hiding behind a thirty second wait.

`scripts/break-pds.sh` and `scripts/break-fhir.sh` drive real failures on
purpose. An alert proved only by a unit test is not proved. Those scripts
found the root-owned dead-letter volume in their first week.

And this week's change, which is the general case. Every alert in this
repository watched a rate, a ratio or a gauge. All of them share one blind
spot: a service that is down exports none of those, so the rules testing for
too much of something go quiet precisely when things are worst. There are now
three rules that watch for absence:

```yaml
- alert: ServiceTargetMissing
  expr: |
    absent(up{job="ingest"})
    or absent(up{job="transform"})
    or absent(up{job="pds-client"})

- alert: BreakerMetricAbsent
  expr: absent(pybreaker_state{name="pds"})
```

The second is the literal guard against the Part 4 defect recurring.
`PDSCircuitOpen` watched `pybreaker_state` for two months while nothing
exported it, and a rule that cannot fire is indistinguishable from a rule that
is never needed. If that gauge disappears again, something says so.

A useful detail: given a selector, `absent()` derives the output labels from
that selector, so the alert names the missing job without any templating. That
only holds for a selector argument; wrap it in an aggregation and the labels
are gone. I confirmed the selector behaviour with promtool rather than
assuming it, after a test failed on exactly that point. There are
twenty promtool cases behind these now, including one asserting that a fully
healthy stack fires nothing at all, which matters because a false alarm on a
working system is worse than no alarm.

## Where it genuinely helped

I want to be as specific about this as about the failures, because "AI is bad
at X" is as lazy as the marketing.

The sweep is real and it is worth a lot. Checking every row of a control table
against the filesystem, every documented command against what the Makefile
does, every external claim against the primary source. That is work I would
not have done by hand, and skipping it is how the documentation got into the
state Part 5 found.

Verification against primary sources, when you insist on it, is reliable. HL7
v2.5.1 escape sequences, the FHIR R4 rules on partial dates and nulls, NHS
Modulus 11, Terraform's `default_tags` syntax, PEP 668, Alertmanager's
`send_resolved` defaults: all checked against the specification rather than
recalled. That rule exists because of a specific failure, a trivy flag in Part
2 taken from memory that broke CI and that I had to fix.

Writing a test that fails against the previous commit before it passes against
the new one is a discipline it will follow consistently once it is the
standing rule, and it caught the one defect in the list above that was caught
before shipping.

And the writing. Seven articles, the runbook, the security model, this
changelog.

## What I would tell someone starting

Put the assistant where reading pays and put yourself where running pays. They
are different jobs, not different quality levels of the same job.

Make it state what it could not check. The single most useful habit in seven
weeks was a standing rule that any unverifiable claim gets said out loud
rather than hedged into the prose. That is how `docs/security.md` ended up
with a "Claimed, not built" section instead of a table of confident sentences.

Be suspicious of a test written in the same pass as the fix. It will encode
the shape of the bug that was found. Ask separately what property you are
claiming, and whether anything tests that property rather than that instance.

Run the thing. Then break it on purpose. Then wait for the email.

## If you want to try this yourself

You need Docker and `make`, about 4 GB free for Docker, and no NHS or cloud
account. Everything uses synthetic test data, and the national lookup is
stubbed by default.

Fork the repository, then:

```bash
make demo
make walkthrough
make break-pds
```

The first two start nine services and prove each step of the tour works. The
third breaks the national patient lookup on purpose. Watch the pipeline keep
accepting messages, the alert fire about two minutes later, and the email land
in a local inbox at `localhost:8025`. Then `make restore-pds` puts it back.

If you are switching careers, into tech or into health tech, that exercise is
the one worth being able to explain out loud: what failed, what kept working,
who got told, and how you know. Most of the interview questions in Part 6 come
down to those four.

Then pick one item from the known limitations in `CHANGELOG.md` and build it.
Authentication on `POST /hl7` is a good first one. That is the step from
following someone else's project to owning a piece of it.

## If you fund or buy health tech

Every finding in this series has a question behind it that you can ask a
vendor, and none of them needs you to read code.

- When did an alert last reach a person, and what did it say? A rule that
  fires into nothing looks identical to one that works.
- Which controls in your security document can you show me in the code? Ten of
  mine could not be shown when I checked.
- What happens to a message when a system you depend on is down? Where does it
  wait, and for how long?
- How do you match a patient whose NHS number changes, or arrives as a Scottish
  CHI number? Mine does not yet, and says so.

A team that answers those with specifics, including what is not built yet, is
telling you more than a feature list can.

## The series in numbers

Over a hundred commits and more than thirty pull requests. About half the
commits, and most of the pull requests, came during the seven weeks of the
series. Ninety-two tests. Twelve alert rules with twenty promtool cases behind
them, each with a runbook section whose link now resolves. Seven articles.

Roughly a dozen defects found by readers, which is the number I would most
want someone starting a build-in-public series to notice.

## What is still not built

`POST /hl7` has no authentication. No part of the AWS configuration has ever
been applied. The dead-letter path does not survive a pod restart, and the
bucket with the correct controls still has nothing writing to it. There is no
Alertmanager in the Helm charts, so Kubernetes alert delivery does not exist.
Patient identity is keyed on the NHS number alone, which is unsafe for exactly
the cases Kevin described.

All of it is in `docs/security.md` under "Claimed, not built", and in
`CHANGELOG.md` under the 1.0.0 known limitations. A tool that is good at
producing confident prose is a reason to be stricter about that list, not
looser.

## Sources

- [Prometheus query functions](https://prometheus.io/docs/prometheus/latest/querying/functions/), for `absent()` and its label derivation
- [Alertmanager configuration](https://prometheus.io/docs/alerting/latest/configuration/), for the `send_resolved` defaults
- [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/)
- [PEP 668](https://peps.python.org/pep-0668/)
- [RFC 3986, section 3.4](https://www.rfc-editor.org/rfc/rfc3986#section-3.4)
- The alert rules in this article were checked with `promtool check rules` and
  exercised with `promtool test rules`; the Alertmanager configuration with
  `amtool check-config` 0.34.1
- `docs/ai-usage.md` in the repository holds the same accounting, maintained
  with the code

## Thank you

To everyone who read a part and told me something was wrong: Kevin Mayfield,
Mustafa Erbay, Nan, Amorizz, upordown.online, Kartik, Paul Lovelace, and the
Part 1 commenters who found the mapping defects. Roughly a dozen real problems in
this repository exist in fixed form because somebody asked a question I had
not thought to ask.

That is the actual finding of this series, and it is not about AI at all. The
assistant read the code tirelessly and found what was wrong in it. Running the
system found what was broken. Other people found what was missing.

Which of those three is weakest in how you work right now?

---

This is a personal project. It is not affiliated with or endorsed by NHS England or any NHS organisation. It uses the public PDS FHIR sandbox and synthetic test data only. Views are my own.
