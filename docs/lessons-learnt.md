# Lessons learnt

A running retrospective across the build of this platform. The point isn't to be triumphalist — it's to capture the things that tripped me, the things that worked better than expected, and the things a future version of me (or a teammate picking this up) would want to know.

Written in the first person on purpose. I've seen too many "post-mortems" buried in passive voice; the honest ones land better.

---

## Day 1 — Foundation and local demo

### What worked

**Stratifying the git history into focused commits was worth the extra keystrokes.** The difference between one `initial commit` and nine conventional-commit steps isn't cosmetic — it's the difference between "here is a pile of code" and "here is a thought process". Interviewers can jump to the commit where I added observability and read only those files. Reviewers can rebase cleanly. Future me can `git bisect` a regression back to a 60-line change instead of a 4,000-line dump.

**Starting with `/healthz` and `/readyz` on every service.** Kubernetes probes are not an afterthought — if I bolt them on at the end, the code around them always ends up ugly. Starting with them forced me to think about *when* a service is ready to receive traffic vs *whether* it's alive. For `ingest`, ready means the downstream HTTP client is initialised; for `transform`, ready means we've connected to HAPI; for `pds-client`, ready is trivially true because it doesn't own state. That's three subtly different meanings for the same probe, and encoding the difference up-front paid off.

**Multi-stage Docker builds from the start.** The runtime image is a `python:3.12-slim` with only the installed packages and the application code — no build toolchain, no source of `requirements.txt`, no compiler. Trivy is quieter, the image is smaller, the attack surface is smaller. One interviewer in the past has asked me *"what's in your final image that doesn't need to be there?"* and being able to answer *"nothing"* is worth the 40 lines of Dockerfile.

**Docker Compose health checks with `depends_on: condition: service_healthy`.** This is the difference between `docker compose up` working and `docker compose up` being a flaky race condition. Without it, the transform service tried to talk to HAPI FHIR before HAPI had finished initialising its Postgres schema — the first run in ten would fail. With it, every run is deterministic.

### What didn't work first time

**HL7 v2 segment terminators.** HL7 v2 uses carriage returns (`\r`) as segment terminators, but every tool I used to craft test messages converted them silently to `\n` or `\r\n`. The first version of `mapper._parse` segfaulted on anything I piped through a heredoc or saved through an editor. The fix was a one-liner — `raw.replace("\r\n", "\r").replace("\n", "\r")` — but the lesson is bigger: **real HL7 messages in the wild arrive with three different line-ending conventions** and any parser that assumes one of them is fragile. Defensive normalisation at the entry point, then everything downstream can assume a canonical form.

**The `hl7` Python library has a surprising API for repeating fields.** PID-3 (patient identifiers) can contain multiple repetitions, each with multiple components. The naive `pid[3]` doesn't tell you which shape you have — it could be a single scalar, a list of scalars, a list of component tuples, or a list of lists. I ended up with a `_field` helper that tries increasingly specific accessors and falls back to string coercion. It's not elegant, but it doesn't crash on real-world messages.

**FHIR transaction bundles and `urn:uuid:` references.** The first version of the mapper used placeholder strings like `"Patient/1"` for internal references. HAPI FHIR accepted the write but the Encounter's `subject.reference` pointed at a nonexistent Patient — because HAPI had rewritten the Patient's id on persist. The fix is `urn:uuid:` references with a matching `fullUrl` in each entry; HAPI resolves them atomically. This is exactly the kind of bug that only shows up when you actually query the resulting bundle, not when you just watch the write succeed.

### What I'd do differently if I started again

**I'd write the DLQ path before the happy path.** I wrote `map_message` first, saw it work, then bolted on the DLQ. That ordering meant the first two commits of the transform service didn't have a failure mode — which isn't how real code lives. Starting with *"every message either succeeds, or lands here with a reason"* would have made the happy path cleaner.

**I'd use SQS from the start, not in-memory queues.** In-memory works for the demo but hides the actual message-ordering question (SQS FIFO vs standard). Swapping now is annotated in the roadmap but means I can't currently demonstrate at-least-once semantics under partition. This is the "production-shaped, not production" trade-off I made consciously to stay within the build budget, but it's the #1 question a senior interviewer asks.

---

