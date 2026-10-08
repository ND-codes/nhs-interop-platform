# Building an NHS Interop Platform: the blog series

This series has seven articles. Each one takes one part of this repository and shows what I built, the best practices behind it, and where AI helped and where it didn't.

The articles are published on dev.to. The drafts live in this folder, and each article links to a Git tag that holds the exact code it describes.

| Part | Article | Planned | Code tag | Status |
| --- | --- | --- | --- | --- |
| 1 | Why NHS trusts need an HL7 v2 to FHIR layer, and how this one is built | 7 Oct 2026 | `part-1` | Planned |
| 2 | From `docker compose up` to Terraform on AWS | 21 Oct 2026 | `part-2` | Planned |
| 3 | Mapping HL7 v2 ADT messages to FHIR R4 | 4 Nov 2026 | `part-3` | Planned |
| 4 | Observability from day one | 18 Nov 2026 | `part-4` | Planned |
| 5 | Security and compliance in a regulated environment | 2 Dec 2026 | `part-5` | Planned |
| 6 | The 10-minute walkthrough, and the questions interviewers ask | 16 Dec 2026 | `part-6` | Planned |
| 7 | Where AI fits, and where it doesn't | 13 Jan 2027 | `part-7` | Planned |

## How to follow along

- Each tag marks the code as it stood when that article went out. To see it, run `git checkout part-1`, using the tag for the article you're reading.
- Work between articles is tracked as [issues labelled roadmap](https://github.com/ND-codes/nhs-interop-platform/labels/roadmap).

## Disclaimer

This is a personal project. It is not affiliated with or endorsed by NHS England or any NHS organisation. It uses the public PDS FHIR sandbox and synthetic test data only, and it contains no real patient data. Views are my own.
