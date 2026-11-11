---
title: "Part 5: The Control That Protected an Empty Bucket"
published: false
tags: security, healthcare, devops, compliance
series: Building an NHS Interop Platform
---

This is Part 5 of a series building an HL7 v2 to FHIR R4 interoperability
service in public. The repository is
[ND-codes/nhs-interop-platform](https://github.com/ND-codes/nhs-interop-platform),
and the code as it stood for this article is tagged `part-5`.

Part 4 was about an alert that watched a metric nothing exported. This part is
about the same failure in documentation. I had written a security document with
a threat table, a Caldicott mapping, a UK GDPR table and a DSPT mapping, all in
the present tense, all with an evidence column. Then I checked every row
against the code.

## The problem with a control table

A security document is read differently from other documentation. Nobody reads
a threat table for inspiration. They read it to find out what is true, usually
because they have to decide whether to trust the system or sign something about
it.

That makes the format itself a risk. Mine put aspiration and fact in the same
table, in the same tense, with the same kind of evidence citation beside each.
There was no way to tell, from the inside, which rows described code and which
described intent. An assessor reading it would have believed all of it.

Ten of those rows did not hold. The most instructive one was not a missing
control. It was a control that was built properly and protected nothing.

## The control that protected an empty bucket

The GDPR table cited an S3 lifecycle rule as the storage-limitation control for
patient data. That rule is real. It is in `infra/terraform/s3.tf`, it moves
objects to infrequent access at 30 days and Glacier at 90, and it expires them
at 2555 days, which is the seven-year NHS records retention. The bucket it
governs is versioned, encrypted with a customer managed key, has public access
blocked and has Object Lock configured. If you were auditing the Terraform, you
would tick that row and move on.

Nothing writes to it.

No service in the repository imports boto3, or any other S3 client. The only
mention of S3 anywhere in the application code is in the transform service's
own docstring, which says the dead-letter path is "S3 in prod". That was an
intention someone wrote down once, and it then got cited in a compliance table
as though it described the system.

In every configuration that actually runs, the dead-letter path is local. Under
Docker Compose it is a named volume at `/tmp/dlq`. In Kubernetes it is an
`emptyDir` mounted at `/tmp`.

Both of those matter more than they look.

An `emptyDir` does not survive the pod. A restart or a reschedule discards it.
So in the deployed configuration, the dead-letter path loses exactly the
messages it exists to preserve, precisely when it is most likely to be
restarted, which is during the outage that filled it. Meanwhile the runbook
told the on-call engineer to "inspect the DLQ bucket" and said messages "will
replay once the write path recovers". There is no bucket to inspect, and
nothing replays.

The Docker Compose case was worse and is the one that got caught first. The
named volume was root-owned while the container runs as uid 10001, so every
dead-letter write failed with `PermissionError` and the message was lost
silently. That was found by running `make break-fhir`, the failure
demonstration script from Part 4, in its first week of existence.

And the dead-letter path is the most sensitive store in the stack. A
dead-lettered message is the complete raw HL7 message: every PID field,
including the ones the mapper deliberately drops before building the Bundle.
The FHIR store holds less patient data than the dead-letter path does. It had
no retention rule of any kind.

So the real compliance position was a well-built seven-year retention control
protecting an empty bucket, while the patient data sat in an ephemeral volume
with no retention, no durability, and a runbook pointing the wrong way.

## The NHS number in four log lines

The second finding is smaller and more common.

The pds-client service logged the NHS number at INFO on every lookup, every
cache hit, every stub response and every upstream error. Four call sites.

What makes it easy to miss is the serialisation. The log format string is
`"%(asctime)s %(levelname)s %(name)s %(message)s"`, which does not mention the
identifier at all. The call looks harmless:

```python
logger.info("PDS cache hit", extra={"nhs_number": nhs_number})
```

`python-json-logger` serialises everything passed in `extra`, whether or not
the format string names it. The line that reaches stdout is:

```json
{"asctime": "2026-11-09 14:07:46,693", "levelname": "INFO",
 "name": "pds-client", "message": "PDS cache hit",
 "nhs_number": "9000000009"}
```

The NHS number is the strongest patient identifier in English healthcare, and
it was going to stdout, which in the Helm deployment means a log store with
different retention and different access control from the patient record
itself. The document, meanwhile, cited Caldicott principle 3 and told the
reader that only the minimum necessary data was used. The National Data
Guardian's wording for principle 2 is that confidential information "should not
be included unless it is necessary for the specified purpose(s)".

It was also going somewhere worse. The outbound call to the PDS sandbox set the
correlation header from the patient:

```python
resp = await http.get(url, headers={"X-Request-ID": nhs_number})
```

A request id is echoed in upstream error responses and recorded by every
intermediary that handles the request. That leaks considerably further than our
own log store.

The fix turned out to be a deletion rather than an addition, and the reason is
the interesting part. The transform service already sends `X-Trace-Id` on every
call to pds-client. The handler signature was `async def lookup(nhs_number:
str)`, which reads no headers, so the trace id was being sent and thrown away.
With the correlation identifier discarded, the NHS number was the only thing
left to log. Accept the header and the patient identifier becomes unnecessary:

```python
async def lookup(
    nhs_number: str,
    x_trace_id: str | None = Header(default=None),
) -> dict:
    trace_id = x_trace_id or str(uuid.uuid4())
```

I did consider logging a hash instead, and decided against it. An NHS number is
ten digits with a constrained check digit, so an unsalted hash of one is
recoverable by brute force almost immediately. A salted hash would work, but it
needs the salt managed as a secret, and it would only be earning back
correlation that the trace id already provides.

## The test that had to look at the bytes

The obvious way to test this is `caplog`. It is the wrong way.

`caplog` inspects the log record. The defect lives in serialisation: the record
carries `nhs_number` as an attribute and the question is whether it reaches
stdout. A `caplog` assertion can pass while the leak continues.

So the tests attach the service's real `JsonFormatter` to a `StringIO` and
assert on the output:

```python
handler.setFormatter(
    jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
)
main.logger.addHandler(handler)
...
assert NHS_NUMBER not in captured_log.getvalue()
```

That is a substring assertion over the exact bytes the container would write,
across the cache path, the stub path, the upstream error path and the open
breaker path. It does not care how the identifier got there, as long as the
test makes that path fail the way it fails for real. It will catch the next
person who adds a fifth log line, including me. Further down is the case where
my first version of this test didn't.

All ten of those tests fail against the previous version of the service and
pass against the current one. That is the only check I trust on work like this.

## Eight runbook links that never resolved

Each of the eight alert rules carried a `runbook` annotation, like this one:

```yaml
runbook: "docs/runbook.md#pds-circuit"
```

None of them resolved. There are no anchor tags anywhere in the runbook, and
GitHub generates a heading's anchor from its text, so the real target for that
section is `#2-pdscircuitopen`. An on-call engineer clicking an alert link
landed at the top of the file.

I did not work this out from the slug rules. I fetched the rendered file from
GitHub and read the anchors out of the DOM, which gave the actual list rather
than my recollection of the algorithm:

```
#1-transformlatencyhigh   #2-pdscircuitopen   #3-dlqratehigh
#4-transformerrorratehigh #5-ingestnotraffic  #6-fieldsdroppedratehigh
#7-invalidnhsnumbers      #8-patientwriteswithoutidentity
```

Three of those eight dead links were mine. I added them in Part 4 by copying
the convention the existing five used, and the convention had never worked. The
sections even carried a line reading `Anchor: #fields-dropped`, which was
decoration. It declared an anchor rather than creating one.

The annotations now point at the generated anchors, every section states its
own link, and the alert rule tests assert the exact annotation string, so a
renumbering that breaks a link breaks a test. That trade-off is written down in
the runbook, because tying a link to a section number is not obviously right.

## The tag that existed only in a comment

The data sensitivity section claimed `DataClass = patient-identifiable` was
applied to every AWS resource through Terraform default tags. The provider
block containing those tags was commented out. The string `DataClass` appeared
exactly once in the whole Terraform directory, inside that comment.

This matters more than cost allocation. The data classification tag is what an
access review or an assessment uses to find the resources holding patient data.
A tag that exists only in a comment finds nothing.

It is now in the live provider block. Two small honesty notes came out of
writing it. The claim can only be "every taggable resource", because several
resources here take no tags argument at all, including `aws_kms_alias` and the
S3 bucket sub-resources. And the block syntax was checked against HashiCorp's
own tutorial rather than written from memory: `default_tags` is a block with a
nested `tags` map and no equals sign. Part 2 went out with a Terraform CI job
that used an invalid trivy flag, because the flag was recalled instead of
looked up, and that is a cheap mistake to stop repeating.

## The one boundary that takes arbitrary input

`POST /hl7` read its body with `await request.body()` and no limit. The document
claimed Pydantic validation at every HTTP boundary, which is true everywhere
except the one place it matters, because an HL7 v2 message is not JSON and has
no model.

It now reads the stream in chunks and stops at 256 KiB. The limit is checked
against `Content-Length` first because that is cheap, and then enforced while
reading anyway, because Content-Length is a claim the client makes and a chunked
request need not send one. There is a test for each of those, plus one that an
exactly-at-the-limit body is still accepted, which is where this kind of check
is usually wrong.

## What I did not fix

`POST /hl7` still has no authentication. Anything that can reach the service can
submit a message that becomes a Patient record. The mitigations that exist are
that the subnets are tagged for an internal load balancer and that NetworkPolicy
restricts pod-to-pod traffic, and neither of those is authentication.

I thought about adding a shared-secret header and decided not to, two days
before publishing. A trust would use mutual TLS or the Spine Secure Proxy, and
shipping an API key check would have produced exactly the thing this article is
about: a row in a table saying authentication is implemented, describing
something nobody would actually deploy. It is in the "Claimed, not built"
section and it is a roadmap issue.

The dead-letter path still has no durability. It has a 72 hour retention
window, pruning on write and on startup, an exported age, an alert and a runbook
entry. It does not survive a pod restart. Fixing that means writing to the
bucket that already has the right controls, which needs an AWS account.

And pruning on write and startup has a hole I should name: a stack that
dead-letters nothing and never restarts keeps its oldest message past the
window. The alert on `hl7_dlq_oldest_seconds` is there because that case should
be visible rather than silent. The actual fix is to move retention to the
storage layer, where it runs whether or not the application does.

## Where AI helps here, and where it doesn't

I build this project with Claude Code, and this week gave a clean reading on
both halves of that.

The sweep is where it earns its place. Reading every row of a control table,
finding the file each row cites, and checking whether the thing is actually
there is tedious, mechanical work that gets skipped precisely because it is
tedious. Most of these ten findings came out of that pass, and I would not have
done it by hand two days before publishing.

The failure mode is sharper, and this part of the series has now shown it
twice.

Part 4 shipped a fix for an alert that could never fire. The fix did not work
either. The breaker was applied as a decorator to a coroutine function, so
pybreaker counted the returned coroutine object as a success and the real
failure raised later, on await, outside the breaker. The breaker never opened,
which means the gauge that had just been added and the alert that had just been
made fireable still could not fire.

The test for it passed. It drove the breaker with a synchronous function, so it
proved the gauge worked rather than proving the breaker worked, which is the
same shape of error as the original bug, one layer down. I only found it by
starting the stack and watching the breaker refuse to open. The commit is
[b43f462](https://github.com/ND-codes/nhs-interop-platform/commit/b43f462).

This week the identical defect turned up in pds-client, and it was only found
because I now knew to look for it. The same fix applies:

```python
with breaker.calling():
    resp = await http.get(url, headers={"X-Request-ID": trace_id})
```

`calling()` is the right API here rather than `call_async`, because
`call_async` is implemented with `tornado.gen.coroutine` and this service runs
on asyncio. That came from reading the installed pybreaker source rather than
assuming.

The runbook anchors are the same failure in a smaller form: a broken convention
reproduced faithfully three times, because reproducing a convention does not
involve testing it.

And while writing this part up, I nearly shipped two more. Before merging, I
had Claude Code review the finished week, and this time the review ran the code
instead of reading it.

The first find was that the NHS number still reached the logs. The test for
the upstream error path raised a connection error, which is the one httpx
exception whose message carries no URL. A real 500 or 429 from PDS raises
`HTTPStatusError`, httpx builds that message from the request URL, and the URL
ends in the NHS number.

Both services logged `str(exc)`, so the leak was open while the test guarding
it passed. They now log the error type, status code and trace id, and the test
makes PDS answer with a real 500.

The second was worse, because it was Part 4's bug again. The new retention
alert fires when the oldest dead-lettered message passes 80 hours, and the gauge
it reads was only updated when pruning ran. Pruning deletes anything past 72
hours, so the gauge could never get past 72, and then froze until the next
write.

The promtool test fed the rule a made-up 290,000 seconds and the rule fired.
That proved the rule and said nothing about the metric, which is the breaker
test from Part 4 all over again. The gauge is now read from disk at scrape
time, and a new test ages a file past the threshold with nothing else
happening.

The pattern across both weeks is that an assistant is reliable at applying a
shape and unreliable at noticing the shape is wrong, and that tests written in
the same pass as the fix inherit the same blind spot. Every time, what caught
it was running the system. That is an argument for the failure demonstration
scripts from Part 4 rather than against the tooling, and it is why `make
break-fhir` found the root-owned volume in its first week.

## Worth stealing

Separate what is built from what is intended, structurally, not with a
qualifier. One table for implemented controls, one section headed "Claimed, not
built". A hedged row still reads as a yes.

Make the evidence column name a file that exists and, for a document, an anchor
you have checked. Four of my rows cited `.gitlab-ci.yml`, a pipeline that
cannot run because the repository is on GitHub. That file is deleted now rather
than left to be cited again.

Point a control at a test where you can. A row that names a test is the only
kind of security documentation that stays true without somebody remembering to
update it.

Test the serialised output, not the record, whenever the question is what
leaves the process. The same applies to metric labels, error responses and
anything that gets rendered on the way out.

Check which store actually holds the data before you write a retention
control. The bucket with the seven-year rule held nothing.

Give a retention rule a metric. A rule nothing measures is a sentence in a
document.

## What I'd do differently

I would have written the security document last. Writing it early, from the
architecture rather than from the code, is precisely how it filled up with
things that were true of the design and not of the system.

I would have audited it when the repository moved from GitLab to GitHub. That
move invalidated four evidence citations in one commit and nobody checked.

And I would have treated the first "in prod" in a docstring as a flag. Both of
this week's worst findings trace back to a sentence describing an intended
future, which later got read as a description of the present.

## Sources

- [The Caldicott Principles](https://www.gov.uk/government/publications/the-caldicott-principles), National Data Guardian, GOV.UK
- [Data Security and Protection Toolkit](https://dsptoolkit.nhs.uk/), NHS England
- [Configure default tags for AWS resources](https://developer.hashicorp.com/terraform/tutorials/aws/aws-default-tags), HashiCorp
- The `calling()` context manager and the Tornado implementation of
  `call_async` were read from the installed pybreaker 1.2.0 source
- The runbook anchor list was read from the rendered file on GitHub, not
  derived from the slug rules

## Next

Part 6 is the ten-minute walkthrough: running the stack from nothing, and the
questions an interviewer actually asks about it.

Have you ever audited a control table against a codebase, and what did you find
that was worse than an empty bucket?

---

This is a personal project. It is not affiliated with or endorsed by NHS England or any NHS organisation. It uses the public PDS FHIR sandbox and synthetic test data only. Views are my own.
