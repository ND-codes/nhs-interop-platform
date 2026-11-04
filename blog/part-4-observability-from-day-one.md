---
title: The Alert That Could Never Fire
published: false
description: Part 4 of 7. An alert sat in this repo for weeks watching a metric no service exported. Fixing it exposed a second silent failure, and a reader supplied a third that no metric can catch.
tags: observability, prometheus, healthcare, devops
series: Building an NHS Interop Platform
---

Since the first week of this project, `observability/prometheus/alerts.yml` has contained this:

```yaml
- alert: PDSCircuitOpen
  expr: pybreaker_state{name="pds"} == 1
```

It is a correct rule. The circuit breaker in front of the Personal Demographics Service opens after five consecutive failures, the pipeline carries on with the demographics from the message, and somebody should be told.

No service has ever exported `pybreaker_state`. The rule has been watching a series that does not exist, which looks exactly like a healthy system.

## The problem

Monitoring has a particular failure mode: you cannot tell the difference between nothing being wrong and nothing being watched. A dashboard with no red on it means one of those two things, and it does not say which.

In this pipeline that matters more than usual, because the failures I care about mostly return 200. A patient written without an NHS number to match on is a successful request. A date of birth that failed to parse is a successful request. The breaker opening and the pipeline silently dropping to unenriched demographics is a whole shift of successful requests.

So this week I went looking for the things the pipeline does that nobody would ever find out about.

## What the pipeline could not see

![Three silent failures: a dead alert, dropped fields, and an unvalidated identity key](UPLOAD-part-4-silent.png)

### The breaker opens and nothing says so

I started the transform service, sent a message, and scraped `/metrics`. Twenty-eight metric families, and `pybreaker_state` is not among them. Nothing with `breaker` or `circuit` in the name at all.

Then I drove the breaker open, which takes five failures:

```
breaker before      : closed | failures: 0
breaker after       : open   | failures: 5
pybreaker_state now : False
```

The breaker is open. The pipeline has stopped enriching. The alert cannot fire, and there is no other signal anywhere.

pybreaker makes this easy to fix. It has a `CircuitBreakerListener` with a `state_change` hook, which is called on every transition, so there is nothing to poll:

```python
class _BreakerStateExporter(pybreaker.CircuitBreakerListener):
    def state_change(self, cb, old_state, new_state) -> None:
        BREAKER_STATE.labels(name=cb.name).set(
            _BREAKER_STATE_VALUES.get(getattr(new_state, "name", None), 0)
        )
```

That `getattr(new_state, "name", None)` is the second version. My first read `new_state.state`, which does not exist in pybreaker 1.2.0. The state objects carry a `name` instead, holding the string `closed`, `open` or `half-open`.

The failure mode of getting that wrong is the thing this whole article is about. `getattr` with a default does not raise. The lookup misses, falls back to `0`, and the gauge sits there reading closed while the breaker is open. I would have shipped a fix for an unfireable alert that left it unfireable, and the metric would have existed, which is worse, because now the dashboard has a panel on it.

The test caught it.

### Fields that fail to map leave no trace

Part 3 was about nine mapping defects. The reason all nine survived is in this article: only a whole-message parse failure goes to the dead-letter folder. A single field that fails to map is simply absent from the resource, and the message returns 200.

I sent a message with an unparseable date of birth:

```
message with an unparseable date of birth -> HTTP 200
birthDate in the resource : False
hl7_dlq_total moved?      : no
any metric for a lost field: none
```

The mapper now reports what it dropped, and transform turns that into
`hl7_field_dropped_total{segment,field,reason}`. The mapper itself stays free of Prometheus imports: it reports, the service counts. Known gaps like PID-11 and PV1-19 come through as `not_mapped`, so they are visible without being alarming, and `unparseable_date` is the one with an alert on it.

### Alert rules are code that only runs during an incident

Which is the actual reason this sat here for weeks. Nothing in the build ever executed that rule.

Prometheus ships a unit test framework for alerting rules, and it took about twenty minutes to use. You give it synthetic series and assert which alerts fire and when:

```yaml
- interval: 1m
  name: PDSCircuitOpen fires once the breaker is open
  input_series:
    - series: 'pybreaker_state{name="pds"}'
      values: "0x5 1x10"
  alert_rule_test:
    - eval_time: 4m
      alertname: PDSCircuitOpen
      exp_alerts: []
    - eval_time: 9m
      alertname: PDSCircuitOpen
      exp_alerts:
        - exp_labels: { severity: warning, team: interop, name: pds }
```

There are nine cases now. The one I would keep if I could only keep one is this:

```yaml
- name: with no series exported the rule is silent, which was the bug
  input_series:
    - series: 'up{job="transform"}'
      values: "1x20"
  alert_rule_test:
    - eval_time: 15m
      alertname: PDSCircuitOpen
      exp_alerts: []
```

That is the repo as it actually stood. The rule is right, there is nothing for it to read, and the test documents that this is indistinguishable from health.

`promtool check rules` and `promtool test rules` now run as a CI job, so the rules are exercised on every pull request rather than during the first outage.

### Watching it fire, rather than trusting the test

A promtool test proves the expression is right. It does not prove the pipeline
produces the series under real conditions, so the last step was to break the
thing on purpose:

```
make break-pds
```

That stops `pds-client`, sends eight messages two seconds apart, and prints the
gauge after each one. Every message returns 200 throughout, which is the whole
point: enrichment has stopped, the stored demographics are now whatever arrived
in the message, and the front door is reporting success.

The first time I ran it against the real stack, the gauge read 0 after all
eight messages. The breaker never opened.

### The fix that still could not fire

The unit test for the gauge passed because it drove the breaker with an
ordinary function. The real PDS call is `async`, and I had wrapped it with
pybreaker's decorator:

```python
@pds_breaker
async def _call() -> dict | None:
    ...
```

pybreaker's only async support is for Tornado, and its README does not mention
asyncio at all. On an `async def` function, the decorator calls it, gets a
coroutine object back, and records a success. The request fails later, when
the coroutine is awaited, outside the breaker. The running service had logged
16 PDS failures and the breaker had counted none of them.

The fix is pybreaker's `calling()` context manager, which wraps the awaited
block itself:

```python
async def _call() -> dict | None:
    with pds_breaker.calling(), PDS_LATENCY.labels(outcome="success").time():
        resp = await http.get(...)
```

There is now a test that goes through the real async path with PDS failing.
It fails against the old code.

It's the same shape as the `getattr` bug, one layer further down. The rule was
right, the gauge was right, the listener was right, and the breaker they all
depended on was never told anything had gone wrong. Every layer had a test and
every test passed.

With the fix in, the breaker opens on the fifth message, and `PDSCircuitOpen`
goes to Firing once the rule's two minutes have passed.

![Prometheus alerts page showing PDSCircuitOpen in the Firing state](UPLOAD-part-4-pds-firing.png)

I had also worried that `for: 2m` could never be satisfied, because the
breaker's `reset_timeout` is 30 seconds and I expected the state to bounce
between open and half-open. My first check of that went through the same
synchronous test path, so it measured nothing real. In the running stack the
gauge held flat at 1 for twelve minutes, until I brought PDS back. pybreaker
only moves to half-open when a call arrives after the timeout, and that trial
call closes or reopens it in the same moment, so a ten second scrape never
sees it.

![Grafana PDS circuit breaker panel stepping from 0 to 1 and back to 0](UPLOAD-part-4-breaker-panel.png)

There is a second script for the other failure path, and writing it corrected
something in my own plan for this article. The plan said to "stop the transform
service mid-burst and show the DLQ". That does not work. The dead-letter folder
is written by transform, so stopping transform means nothing writes to it at
all: ingest logs the failure and answers 200 with `forwarded: false`. To fill
the dead-letter folder you have to break what transform writes to, which is
HAPI FHIR. `make break-fhir` does that.

Its first real run found one more. Every dead-letter write failed with
`PermissionError`. The service runs as an unprivileged user, and the Docker
volume mounted over its dead-letter folder belonged to root, because the image
never created that folder. So the messages the folder exists to keep were the
ones being lost. The image now creates the folder, owned by the service user,
and Docker copies it into a new volume when the volume is first created.

![Prometheus alerts page showing DLQRateHigh in the Firing state](UPLOAD-part-4-dlq-firing.png)

### The panels, which I nearly forgot

Four new metrics and, for most of a day, no way to see any of them. The Grafana
dashboard had six panels and not one showed the breaker state, the dropped
fields, the invalid NHS numbers or the write identity.

That is a fairly pointed thing to nearly ship in an article about not being able
to see things. The series existed and the alerts worked, and the place people
actually look had not changed at all. There are ten panels now.

## The one a reader found

Kevin Mayfield, who does health integration and data architecture for a living, read Part 3 and pointed out something I had not considered at all.

I had just shipped a fix that keys patient identity on the NHS number, as a conditional update. He pointed out that his trust is near the Scottish border, and that a Scottish CHI number sometimes arrives in the NHS Number field. More than that: a patient's NHS number can change when they move between the Scottish and English NHS.

Which means the identity key I had just built the whole write path around is not stable.

I tested it. Same patient, same hospital number, two messages with different NHS numbers:

```
message 1 request : PUT Patient?identifier=...nhs-number|1111111111
message 2 request : PUT Patient?identifier=...nhs-number|9000000009
same target?      : False
local MRN in both : L12345, L12345
```

Two records. The hospital number is identical in both messages and is simply not looked at. Nothing counts it, nothing alerts on it, and the FHIR server returns 200 twice.

That led somewhere I should have gone in Part 3. If the NHS number is the identity key, is it ever validated?

```
9000000009   checksum valid: True   accepted: True
1111111111   checksum valid: True   accepted: True
1234567890   checksum valid: False  accepted: True
```

No. An NHS number carries a Modulus 11 check digit, and the NHS Data Dictionary is unambiguous about it: "The check digit is validated using the Modulus 11 algorithm and the use of this algorithm is mandatory." Multiply the first nine digits by 10 down to 2, sum, take the remainder modulo 11, subtract from 11. A result of 11 means a check digit of 0, and "If the result is 10 then the NHS NUMBER is invalid and not used".

I was not running it. `1234567890` was being accepted as an NHS number and used to match a patient.

This is also a partial answer to Kevin's CHI point, which is why it is worth doing beyond tidiness. A CHI number is also ten digits, and it will not satisfy the NHS check digit. So a sending system supplying CHI numbers in the NHS Number field now shows up as a rate on `nhs_number_invalid_total` rather than as silently mismatched patients.

An identifier that fails the check is still kept on the resource, because the sender sent it and dropping it loses information. It is just never used to look a patient up or to match an existing record. A wrong identity is worse than no identity.

Writes with nothing to match on are now counted as `patient_write_total{identity="none"}`, and there is an alert when that passes a tenth of writes.

## What none of this catches

The thing Kevin actually described is not a metric problem.

Counting writes with no identity tells you when matching is degrading. It does not tell you that the same person has arrived twice under two different NHS numbers, because from inside a single message that looks completely normal. Both messages are valid, both have good check digits, both write successfully.

Catching that needs a matching step, which is what he does:

> I have a scoring systems based on identifier, date of birth, gender, name and post code. I weight MRN and NHS the highest, then DOB.

A demographic query first, then a weighted score across several fields, rather than trusting one identifier. He also raised the fetus and baby case, where a newborn may not have its own NHS number yet, which breaks identifier-first matching in a different direction.

That is a substantial piece of work and it is not in this week's change. What this week does is make the conditions that feed it visible, so when the matching goes in there is something to measure it against.

## Worth stealing

Test your alert rules. They are the only code in the repo that runs exclusively during an incident, which makes them the least exercised and the most load-bearing. `promtool test rules` exists and takes an afternoon to adopt.

Assert the series exists, not just that the rule is right. My rule was correct the whole time. The failure was one level below it, and no amount of reviewing the expression would have found it.

Watch for silent fallbacks in monitoring code: `getattr` with a default, or a synchronous decorator around an `async` function. Both fail by doing nothing, in the thing that is supposed to tell you about silent failures. My gauge read zero for the same reason the alert read quiet.

Count the successes you are unsure about. Error rates are the easy half. `patient_write_total{identity="none"}` counts requests that returned 200 and did something I would rather they had not.

Export the metric and add the panel in the same change. I had the series, the alert and the rule test before I noticed the dashboard still showed the same six panels it had in week one. A metric nobody has a graph for is halfway back to where this started.

## Where AI helps here, and where it doesn't

I built this project with Claude Code. This week I had it write the metrics, the promtool tests and the break scripts, then made it prove each one against the running stack, not the code.

Writing the listener, the metrics and the promtool cases was quick. The useful part was the step before: starting the service, scraping `/metrics` and reading the list of what was actually there, rather than reading the alert file and the service code and believing they lined up. They had not lined up for weeks, and both halves looked correct on their own.

It also wrote the async decorator bug, and the first timing check that went through the same synchronous path. Both were caught the same way, by running the real stack and reading what it did.

Four weeks in, the pattern is stable enough to state plainly. Every finding in this series has come from running the thing, and the two most valuable have come from people who read it and knew more than I did about the domain. Neither of those is a code review.

## What I'd do differently

I would have written the alert rule tests at the same time as the alert rules, in the first week. Not because the rules were wrong, but because writing the test is what forces you to produce the series, and producing the series is what I had skipped.

I would also have written the break scripts first. `make break-pds` is about thirty lines and it is the only thing here that tests the whole chain, from a stopped container through to a rule evaluating in Prometheus. Everything else in this article tests one layer and assumes the next one. I would also have run it before tagging this part, not after.

And I would have remembered that fixing the code does not fix the data it already wrote. The demo database held 996 copies of the same test patient from before Part 3's conditional update. The conditional update then refused, correctly, to pick one, with the 412 the FHIR spec requires. I reset the demo database. A trust can't, and that is where the matching work Kevin described begins.

## Sources

- [NHS Data Dictionary: NHS NUMBER, including the Modulus 11 check](https://archive.datadictionary.nhs.uk/DD%20Release%20May%202024/attributes/nhs_number.html)
- [Prometheus: unit testing rules](https://prometheus.io/docs/prometheus/latest/configuration/unit_testing_rules/)
- [Prometheus: alerting rules](https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/)
- [pybreaker on PyPI](https://pypi.org/project/pybreaker/)
- [pybreaker README: the `calling()` context manager and Tornado-only async support](https://github.com/danielfm/pybreaker#readme)
- [FHIR R4: conditional update, including the 412 on multiple matches](https://hl7.org/fhir/R4/http.html#cond-update)
- [Docker: populating a volume from a container](https://docs.docker.com/engine/storage/volumes/)
- [prometheus_client for Python](https://github.com/prometheus/client_python)
- [The code for this article, tag part-4](https://github.com/ND-codes/nhs-interop-platform/tree/part-4)

## Next

Part 5, on 11 November, is security and compliance, which has a backlog of its own: two documents in this repo describe a `DataClass` tag applied by a Terraform block that is commented out.

Which of your alert rules has never fired, and do you know whether that is good news?

---

This is a personal project. It is not affiliated with or endorsed by NHS England or any NHS organisation. It uses the public PDS FHIR sandbox and synthetic test data only. Views are my own.
