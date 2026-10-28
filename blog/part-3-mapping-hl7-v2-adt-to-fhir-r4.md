---
title: Nine Ways an HL7 v2 to FHIR Mapping Goes Quietly Wrong
published: false
description: Part 3 of 7. Every one produced a FHIR Bundle a server would accept while holding the wrong thing, including a positive culture result stored as the number zero. Three of the nine were found by readers of Part 1.
tags: healthcare, fhir, python, testing
series: Building an NHS Interop Platform
---

Part 1 built the translation layer. Part 2 built the pipeline that checks it. This part is about the translation itself, which turns out to be where the interesting failures live.

None of what follows threw an exception. Every one produced a FHIR Bundle that a server would accept, containing the wrong thing. That is the particular danger of mapping work: the output is always well-formed, so the only way to find the problems is to run the mapper and look at what comes out.

I found nine. One of them stores a positive culture result as the number zero, and three of the nine I did not find at all: readers of Part 1 did.

## The problem

A mapping is a long list of small decisions, and each one looks obviously right while you are making it. PID-5 is the name. PID-7 is the date of birth. You write the line, the test passes, you move on.

What makes it dangerous in a clinical setting is that the failures are silent and plausible. A missing middle name is not an error. A date of birth that vanished is not an error. The Bundle validates, the server returns 200, and the wrong record is now in the system of record with every appearance of being right.

So this week I stopped reading the mapper and started feeding it things.

## What I found

![A table of nine mapping behaviours, each failing before the change and passing after](UPLOAD-part-3-before-after.png)

Nine behaviours, each checked by running the real mapper on a real message:

```
behaviour                                before   after
--------------------------------------------------------
middle name kept                          FAIL     pass
partial date YYYYMM kept                  FAIL     pass
dob with tz offset kept                   FAIL     pass
escape sequence decoded                   FAIL     pass
fullUrls are real UUIDs                   FAIL     pass
patient resend updates, not duplicates    FAIL     pass
text result not made numeric              FAIL     pass
no JSON null in the resource              FAIL     pass
sex A maps to other, per HL7              FAIL     pass
```

### A positive culture result became zero

This is the one I would want to know about if it were my trust.

An OBX segment carries a result. OBX-2 says what type the value is: `NM` for a number, `ST` for text, `CE` for a coded entry. The mapper ignored OBX-2 entirely and tried to turn every value into a `valueQuantity`. When the text would not parse as a number, it did this:

```python
except ValueError:
    return {"value": 0, "unit": units or "", "comparator": None, "_raw": value}
```

So a microbiology result of `POSITIVE` came out as:

```json
{ "valueQuantity": { "value": 0, "unit": "", "comparator": null, "_raw": "POSITIVE" } }
```

A qualitative result became the number zero. Anything downstream reading `valueQuantity.value`, a chart, a rule, a dashboard, sees a measurement of nought. `_raw` is not a FHIR element either. It was invented, so a conformant server is free to reject the resource or drop the property, and with it the only surviving copy of the actual result.

The fix is to read OBX-2 and pick the matching `value[x]`:

```python
if vt in _CODED_VALUE_TYPES:
    return {"valueCodeableConcept": {"text": value}}
if vt in _NUMERIC_VALUE_TYPES or vt == "":
    try:
        quantity = {"value": float(value)}
    except ValueError:
        return {"valueString": value}
    ...
return {"valueString": value}
```

`POSITIVE` is now `{"valueString": "POSITIVE"}`. A segment that claims `NM` and carries text also becomes a string, because inventing a number is the failure mode I am trying to get away from.

### Every admission created a new patient, and the obvious fix is the wrong one

The Bundle posted the Patient like this:

```json
{ "method": "POST", "url": "Patient" }
```

An unconditional create. Send the same admission twice, get two patients. Resend a day's traffic after an outage, get a duplicate for every message.

This one came from a reader of Part 1. Anjan Kumar Gundaboina asked on LinkedIn whether the mapper was idempotent, or whether a resend produced a duplicate Patient. It produced a duplicate.

The obvious fix is a conditional create. FHIR has `Bundle.entry.request.ifNoneExist`, defined as "Instruct the server not to perform the create if a specified resource already exists". One match returns 200 and creates nothing, so a resend stops duplicating.

That is not enough, because an A08 is a patient update message. If the patient exists and the message carries a corrected date of birth, a conditional create looks at the one match and does nothing at all. The duplicate is gone and so is the correction.

A conditional update handles both. FHIR defines it as `PUT [base]/[type]?[search parameters]`, where with no matches "The server creates the resource", with one match "The server performs the update against the matching resource", and with several it returns 412.

```json
{
  "method": "PUT",
  "url": "Patient?identifier=https://fhir.nhs.uk/Id/nhs-number|9000000009"
}
```

When there is no NHS number, there is no condition and it stays a plain create. A condition that matches everything is worse than none: it would merge every patient without an NHS number into one record.

### The dates it threw away

PID-7 is a time stamp, not a date, and senders fill in as much as they have. The mapper accepted `YYYYMMDD` and returned nothing for anything else.

Readers raised this one too. Lee Wilson pointed out that in many EHRs only the year of birth is known, and Anjan named the partial-date fallback as exactly the kind of silent failure he would want a dashboard on.

FHIR is more accommodating than the mapper was. It defines `date` as "A date, or partial date (e.g. just year or year + month)" in the format "YYYY, YYYY-MM, or YYYY-MM-DD".

So `198001` should be `1980-01`. It was becoming nothing at all. So was `19800101120000+0100`, a perfectly ordinary full time stamp with a time zone offset, because the offset did not match any of the three formats being tried. A date of birth with a time zone on it was silently dropped.

Both now map. The offset is stripped, because a FHIR date "SHALL be no time zone".

### The names it mangled

HL7 reserves five characters as delimiters and defines five escape sequences so they can appear inside a field anyway. `\T\` is the subcomponent separator, so `O\T\BRIEN` is how the wire carries O&Brien. The mapper stored the escape sequence.

One subtlety is worth getting right. `\E\` stands for the escape character itself, so decoding has to be a single pass. Replace `\F\` first and then `\E\`, and the sequence `\E\F\E\`, which means the literal text `\F\`, becomes a field separator instead. Sequences outside the five, like `\H\`, are passed through rather than stripped.

### The middle name, and the test that approved of losing it

PID-5 is `DOE^JOHN^A`. Component 3 is the middle name. FHIR puts given and middle names together in `given`, in order.

The mapper read component 2 and stopped, so `A` was dropped. That is the least serious of the nine and the most instructive, because of what I found in the test file:

```python
assert patient["name"][0]["given"] == ["JOHN"]
```

The test asserted the bug. It had passed since the day it was written, because it was written by reading what the mapper did rather than what the segment said. A green suite told me the mapping was right, and what it actually told me was that the mapping had not changed.

### The identifiers that were not identifiers

Each Bundle entry had a `fullUrl` like `urn:uuid:patient-1`. FHIR's `uuid` type is "A UUID (aka GUID) represented as a URI", meaning RFC 4122. `patient-1` is not one. HAPI accepted them, which is why it survived; a stricter server need not. They are real UUIDs now.

### Nulls where FHIR forbids them

When PID-7 would not parse at all, `birthDate` came out as `None` and serialised to `"birthDate": null`. FHIR's JSON rules say properties "never have null values" and to "Omit a property if it is empty".

Testing that turned up a second case: a PID with no name at all produced `"family": ""`. The same page says "String property values can never be empty".

Both are now pruned on the way into the Bundle, along with empty arrays and objects. Zero and `false` survive the pruning, because a quantity of 0 is a real measurement and that is the mistake this article started with.

### The answer I said did not exist

I had written PID-8 up as an open question. HL7 table 0001 carries `A` for ambiguous and `N` for not applicable, FHIR's `administrative-gender` has only `male`, `female`, `other` and `unknown`, and I concluded there was no clean target and left the mapper sending both to `unknown`.

Then Dalisay Giffard suggested on LinkedIn that I check the PID-8 mapping, because administrative sex has a different meaning to gender. The field held up: FHIR R4 defines `Patient.gender` as administrative gender, and that is where HL7's v2-to-FHIR guide puts PID-8. The codes did not. Checking them properly, I found that HL7 publishes a concept map for exactly this, in the v2-to-FHIR implementation guide. It sends `A` to `other` and `N` to `other`, and rates both "is equivalent to".

Which is obviously right once you see it. `unknown` says we do not know. The sender did know; the answer was simply neither male nor female. I had been treating a recorded answer as a missing one, and I had reasoned my way to a conclusion the standard had already settled in a table I had not looked for.

## The map was not the territory

There is a `docs/hl7-fhir-mapping.md` in the repo, a careful table of HL7 fields and their FHIR targets. I checked each row against the running mapper. Three described mappings that do not exist: PID-11 to `Patient.address`, RXE-4 to `dosageInstruction`, RXE-13 to `requester`. It also called `_raw` an extension, which gave an invented property the air of deliberate design.

That is three weeks running where the most useful finding was a document describing a system nobody had checked. Week 2 it was a security scanner with no rules loaded. This week a mapping table describing a mapper I had not written.

The rewritten version says "not mapped" where that is the truth, and every row was produced by running the thing.

## What readers found that I have not fixed yet

Part 1 and Part 2 are public, and so is the repo, which means the mapping has had more eyes on it than mine. Several more things came back that are not in this week's change, named here so they do not quietly disappear.

PID-3, raised by Kevin Mayfield, Ian Haywood and Lee Wilson. PID-3 reads only CX-1 and CX-4. The CX-5 identifier type code, `NH` or `MR`, is dropped, there is no `use`, and every non-NHS identifier lands in one `local-patient-identifier` system with no assigner, so medical record numbers from two different trusts collide. The [North West GMSA HL7 v2 guide](https://nw-gmsa.github.io/en/hl7v2.html#pid) treats CX-1, CX-4 and CX-5 as mandatory and uses the ODS code as the assigning authority. My fixture uses `GENERAL_HOSP`, which is not an ODS code. Ian's warning was the practical one: unless the namespace carries through, you'll have fun later when another source uses the same ID. Kevin added that the MRN system should be a URI or an OID, that EPR vendors and trusts have each made their own choice, and that his organisation uses a URI.

Replay ordering, which follows from Anjan's question. Now that writes update rather than create, replaying an older dead-lettered message after a newer one would overwrite newer data with older. The fix I have in mind is to check MSH-7 against what was last applied, at the point of the write. Using the NHS number as an SQS FIFO message group ID orders live traffic but does nothing for a replay out of the dead-letter folder.

Field-level failures are silent, which Anjan asked about directly. Only a whole-message parse failure dead-letters. A single field that fails to map produces no flag, no metric and no log line, which is how all nine of these survived.

No canonical model, which Mitchel Coon asked about. The mapper goes straight from segments to FHIR resources, with no intermediate representation, so every quirk is handled inline. HL7's own v2-to-FHIR guide maps directly too, so this is defensible, but it is why the fixes above are scattered across the file rather than sitting in one place.

Lee also pointed out that the NHS number is not guaranteed, that address fields are often misused, and that phone fields can hold free text like "don't call after 6pm". Without an NHS number the Patient falls back to a plain create, so those patients still duplicate. PID-11 and PID-13 are not mapped at all yet, so the free text is a problem waiting for whoever maps them.

## The one thing still open

PID-8 is settled and the duplicates are fixed. One judgement call is left, and I would rather leave it visible than decide it quietly in code.

The Encounter has no identifier and no period, so a resent admission duplicates it exactly the way the Patient used to.

Mustafa Erbay made the right suggestion in the comments on Part 1 here on dev.to: use PV1-19, the visit number, rather than building a composite key out of the fields that happen to be present. It is the natural identity for a visit and it is what the segment is for. The reason it is not in this week's pull request is the case he and I did not settle, which is what to do when PV1-19 is absent, since the standard does not require it. A conditional create with an empty condition matches every Encounter that also lacks a visit number and merges them, which is a worse failure than the duplicates I have now. So the duplicates stay until the absence case has an answer.

## Worth stealing

Feed the mapper, don't read it. Every one of these nine produced valid output. Reading the code tells you what it says. Running it tells you what it does, and in mapping work those come apart constantly.

Write the test from the specification, not from the output. The middle-name test passed for months because it was written by running the code and pasting the result. A test written from PID-5's definition would have failed on day one.

Check the mapping document against the mapper. A mapping table is the thing colleagues trust most and verify least, because it reads like a specification when it is actually a description, and descriptions drift.

Look for the concept map before you reason from first principles. I decided PID-8 had no clean FHIR target and wrote that conclusion into a document. HL7 had already published the answer as a table. The standards bodies have done more of this work than it is comfortable to assume.

Let the message decide the shape. OBX-2 exists precisely to tell you what kind of value you are holding. Guessing with `float()` and falling back to zero is how a culture result becomes a number.

## Where AI helps here, and where it doesn't

I built this project with Claude Code. [Felix: one line on how you used it this week.]

The fixes were quick to write once I knew what was wrong. The escape decoder, the date precision logic, the OBX-2 branch: all small, well-specified, mechanical.

Finding them was a different activity, and it was not reading. It was constructing awkward messages, running them through, and looking at the JSON. A patient with a middle initial. A date of birth with a time zone. A name with an ampersand. A culture result that says POSITIVE. None of those are exotic, and every one of those defects is invisible in a code review, because each line is individually reasonable.

That is now three weeks where running the thing found what reading it could not. I am going to stop describing it as a surprise.

The other half is less comfortable. Three of the nine I did not find by any method, and would not have: they came from people who read Part 1 and asked questions I had not thought to ask. Publishing the thing in an unfinished state is the only reason those arrived at all, which is an argument for writing the series while the work is still wrong rather than after it is right.

## What I'd do differently

I would have written the mapping document and the tests from the HL7 and FHIR specifications first, and the mapper second. I did it the other way round, and ended up with a test suite and a document that both described the implementation back to me. Two sources of confirmation, neither of them independent.

## Sources

- [HL7 FHIR R4: Data Types](https://hl7.org/fhir/R4/datatypes.html)
- [HL7 FHIR R4: Bundle, element definitions](https://hl7.org/fhir/R4/bundle-definitions.html)
- [HL7 FHIR R4: RESTful API, conditional create](https://hl7.org/fhir/R4/http.html)
- [HL7 v2.5.1, Chapter 2: Control, escape sequences (hosted by HL7 Europe)](https://www.hl7.eu/HL7v2x/v251/std251/ch02.html)
- [HL7 v2 table 0001, Administrative Sex (HL7 Terminology)](https://terminology.hl7.org/CodeSystem-v2-0001.html)
- [The code for this article, tag part-3](https://github.com/ND-codes/nhs-interop-platform/tree/part-3)

## Next

Part 4, on 4 November, is weekly rather than fortnightly from here, and it is about observability, including an alert in this repo that has never been able to fire.

Which of your mappings has a test that was written by pasting in what the code already did?

---

This is a personal project. It is not affiliated with or endorsed by NHS England or any NHS organisation. It uses the public PDS FHIR sandbox and synthetic test data only. Views are my own.
