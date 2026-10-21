---
title: From docker compose up to Terraform on AWS, and the CI That Checks It
published: false
description: Part 2 of 7. What has to be true between a stack that runs on a laptop and one that runs on AWS, and what a CI pipeline found in my own repo on its first day, including a live API key.
tags: devops, terraform, aws, healthcare
series: Building an NHS Interop Platform
---

Part 1 ended with a pipeline that turns HL7 v2 into FHIR R4 and runs on a laptop with `make demo`. This part is about the distance between that and something a trust could run, and about the checks that tell you whether you have crossed it.

The short version: the distance is larger than the Terraform makes it look, and the first thing I did this week was build the pipeline that proves it.

## The problem

`docker compose up` gives you a working system and almost no evidence. It tells you the code runs. It tells you nothing about whether the database is reachable from the internet, whether a credential is sitting in the history, or whether the thing you deploy is the thing you tested.

In most settings that gap is a nuisance. In an NHS setting it is the whole job. The data is patient-identifiable, the questions an auditor asks are about provenance, and "it worked on my laptop" is not an answer to any of them.

So the move from compose to AWS is not really a move from one runtime to another. It is a move from a system you have seen working to a system you can show is working. That needs two things: infrastructure described as code, and a pipeline that checks it on every change.

I had the first. I did not have the second, and this week showed me what that was costing.

## What I built

The repo describes an AWS deployment in Terraform: a VPC with public, private and database subnets, an EKS cluster with a managed node group, an RDS Postgres instance for the HAPI FHIR server, an S3 bucket for the dead-letter path, and KMS keys. `terraform plan` reports 46 resources.

![The local stack on the left and the AWS target on the right, with the CI pipeline between them](UPLOAD-part-2-laptop-to-aws.png)

Two honest notes before anything else.

The provider block in `infra/terraform/versions.tf` is this:

```hcl
provider "aws" {
  region = var.aws_region

  access_key = "mock"
  secret_key = "mock"

  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true
}
```

That is how the plan runs in CI without an AWS account. It hasn't always been like that. In April I ran `apply` against my own AWS account to see the stack come up, and that is where the EKS add-on version conflicts in `docs/lessons-learnt.md` came from. Nothing from that run is still up, so the code you can clone today has been planned, not deployed. The mock credentials keep it free for anyone else to run.

The second note is worse, and the pipeline found it.

## The pipeline, and what it found

Until this week the repo had a `.gitlab-ci.yml` describing five stages, and nothing had ever run it. Porting it to GitHub Actions took an afternoon. Getting it green took longer, because it kept being right. Some of what it found turned up while I was porting it, and the rest arrived on its first real run.

### The secret scan had never scanned anything

The pipeline runs Gitleaks. The repo has a `.gitleaks.toml`. The file held exactly this:

```toml
# Gitleaks config - ignore false positives in vendored terraform modules
```

One comment. No rules, and no `[extend]` block. Gitleaks' documentation describes two options for a custom config: "define your own configuration, default rules do not apply", or extend one, where "useDefault will extend the default gitleaks config built in to the binary".

Mine was the first kind. A configuration with no rules, replacing roughly a hundred built-in ones.

I checked it rather than assuming. A directory with a planted Slack bot token, scanned three ways with gitleaks 8.28.0:

```
no config file                  leaks found: 1    exit 1
the repo's .gitleaks.toml       no leaks found    exit 0
[extend] useDefault = true      leaks found: 1    exit 1
```

The middle line is the one that matters. For months, a secret scan ran, found nothing, and the nothing meant nothing. The repo also carries `docs/verification/gitleaks.json`, a stored run showing zero findings, which I had been treating as evidence that the repo was clean. It is evidence that the scanner was switched off.

When I first wrote this section, the next line said I had no reason to think there was a secret in there. Then the fixed scanner ran over the git history and found one.

On 26 April I committed a PDS API key to `.env.example` while getting a demo working. The next commit blanked the value. The file looked clean from then on, and the key sat in the public history for six months. A scanner pointed at the current files would never have seen it. `gitleaks git` reads every commit, and it did.

The key is revoked now. It stays in the history, because rewriting a public history breaks every clone and tag. Revoking it is what makes it harmless. The repo's `.gitleaksignore` now holds that one finding's fingerprint, with a comment saying when the key was committed and when it was revoked. Any other secret still fails the build.

So the stored report with zero findings wasn't just meaningless. It was wrong, and I had been treating it as evidence.

### The test job could not have passed

The GitLab test job runs:

```
pytest -q --junitxml=junit.xml --cov=. --cov-report=xml
```

`pytest-cov` appears in none of the three `requirements-dev.txt` files. I ran that command against each service in a clean virtualenv built from its pinned requirements, and got the same thing three times:

```
error: unrecognized arguments: --cov=. --cov-report=xml
```

The tests themselves passed. Sixteen of them pass as soon as you drop the flags. It was the job around them that had never worked, and because nothing ran it, nothing said so.

With `pytest-cov` added, the numbers are ingest 98 per cent, transform 83 per cent, pds-client 93 per cent.

### The database could call the internet

The new pipeline runs Trivy against the Terraform. Its first finding was critical, in the security group in front of the FHIR database:

```hcl
resource "aws_security_group" "rds" {
  description = "HAPI FHIR Postgres, ingress from EKS nodes only"

  ingress {
    description     = "Postgres from EKS"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [module.eks.node_security_group_id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
```

Look at the care in the ingress rule and the shrug in the egress one. Inbound is locked to the node group's security group, port 5432, TCP only. Outbound is every port, every protocol, every address on the internet.

The description even says "ingress from EKS nodes only", which is true and beside the point. I wrote a careful inbound rule, felt careful, and pasted the default outbound rule underneath it.

The fix restricts egress to the VPC.

### The cluster API was open to the internet

My first count of Trivy findings was wrong. I ran it on a copy of the repo where it never downloaded the community EKS module, so it only scanned my own files. A clean checkout in CI downloads the module and scans what my inputs do to it. That found three more critical findings.

Two were the same problem. The EKS API endpoint was public and open to `0.0.0.0/0`:

```hcl
  # API endpoint is private by default; grant access to the CI runner via
  # the public endpoint + CIDR allow-list when needed.
  cluster_endpoint_public_access       = true
  cluster_endpoint_public_access_cidrs = ["0.0.0.0/0"] # tighten in prod
```

The comment says private. The code says public, to everyone. Nothing outside the VPC needs that endpoint, because Argo CD pulls from inside the cluster, so it is now private.

The third was node egress to any address, the same rule as the database. This one I kept. The nodes need outbound internet to pull images and to call the PDS API. It sits in a `.trivyignore.yaml`, scoped to the one module file and with the reason written next to it. I checked the scope by putting the database rule back the way it was: the build failed.

### A dependency I never chose

The image scan failed on all three services for the same reason. `fastapi==0.115.0` pulls in starlette 0.38.6, which has CVE-2024-47874, rated high. I had pinned FastAPI and never looked at what it brought with it.

Bumping FastAPI alone wasn't enough. The newer starlette releases had high findings of their own, fixed only in 1.x, and `prometheus-fastapi-instrumentator` 7.0.0 capped starlette below 1.0. Both had to move. All sixteen tests still pass, and coverage hasn't changed.

What is left on the Terraform is four findings, none above medium: IAM authentication and deletion protection on the database, a customer-managed key for Performance Insights, and access logging on the bucket. Those are real and they are on the list for Part 5. Every run prints them, along with the accepted one, so they don't live only in my head.

![The seven CI jobs, which ones gate the merge, and what the first runs found](UPLOAD-part-2-ci-jobs.png)

## What the pipeline actually checks

Seven jobs run on every pull request into `master`:

1. `lint`, ruff over each service.
2. `test`, pytest with coverage over each service.
3. `secrets`, Gitleaks over both the working tree and the git history.
4. `sast`, Semgrep with the `p/ci`, `p/python` and `p/security-audit` rulesets.
5. `terraform`, `fmt -check`, then `init -backend=false` and `validate`.
6. `iac-scan`, Trivy against the Terraform.
7. `image`, a Docker build per service, then a Trivy image scan and a Syft SBOM.

A few decisions inside that are worth more than the list.

Every version is pinned, down to the scanner binaries. The gitleaks problem was a security tool that silently stopped checking, and a floating `latest` tag is the same failure wearing a different hat. If a tool changes what it catches, I want that to arrive as a version bump I can read in a diff.

`terraform validate` runs with `-backend=false`, which initialises the providers without touching remote state, so the job needs no AWS credentials at all. Validation is a question about syntax and types, not about an account.

The IaC scan reports everything and fails only on high and critical. A gate you have not triaged is a gate someone turns off in a week. Report first, then promote findings to blocking as you fix them.

The image is built and scanned but not pushed. There is nowhere to push it to yet, and inventing a registry to make the diagram look complete would be the same instinct that produced the egress rule.

And one job did not come across at all. The GitLab pipeline ended with a `deploy` stage that bumped a Helm values file and let Argo CD sync it. Its rule was `if: $CI_COMMIT_BRANCH == "main"`. This repo's branch is `master`. That job could never have run on any commit, which is a tidy summary of the whole week: the pipeline described a system nobody had checked.

## Best practices worth stealing

Commit the Terraform lock file. HashiCorp's documentation says "You should include this file in your version control repository", because Terraform "remembers the decisions it made in a dependency lock file" so that "it can (by default) make the same decisions again in future". Mine was in `.gitignore`. CI is exactly the second machine that needs to make the same decision.

Make the local command and the CI command the same one. The repo's Makefile already had `make tf-validate`, running `init -backend=false`, `validate` and `fmt -check`. The CI job runs the same three. If it passes on your machine it passes in CI, and you stop pushing commits to find out.

Scan the history, not just the tree. `gitleaks dir` checks what is there now. `gitleaks git` checks what was ever there. A secret removed in a later commit is still published. While you are at it: `detect` is deprecated, and the README says "v8.19.0 introduced a change that deprecated `detect` and `protect`". The modes are `git`, `dir` and `stdin` now.

Make the tool fail loudly or not at all. Semgrep prints its findings and exits 0 unless you pass `--error`. Trivy does the same unless you pass `--exit-code 1`, and it is in two of my jobs. So three of the seven would have been decorative by default: they would have printed the problem in a log nobody opens and gone green.

Gitleaks is the exception, and I had it wrong until I tested it. Its help text reads `--exit-code int  exit code when leaks have been encountered (default 1)`, which I first skimmed as the flag being required. It is not: gitleaks exits 1 on a finding whether you ask or not. I had already written the opposite into a draft of this article. Checking the three tools took one command each.

Read why a gate went red. My first `iac-scan` job passed `--no-progress` to `trivy config`. That flag works on `trivy image` and doesn't exist on `trivy config`, so the job failed before it scanned a single file. Red, for a reason that had nothing to do with the Terraform.

Fix the name, not the rule. Semgrep flagged `requests.append({... "http://snomed.info/sct" ...})` as an unencrypted HTTP request. It was a Python list called `requests`, holding FHIR MedicationRequests, and the URL is a SNOMED CT system identifier that FHIR requires in that exact form. Renaming the list to `medication_requests` cleared it without switching anything off.

## Where AI helps here, and where it doesn't

The port was a good use of AI. Translating a GitLab pipeline into Actions is mechanical, well-documented work, and the shape of the answer is known before you start.

What it did not do is notice. The gitleaks config, the key in the history, the missing `pytest-cov`, the egress rule and the open endpoint were all found by running things and reading the output, not by looking at the code and thinking about it. Every one of those files reads as correct. A config file with an allowlist comment looks like a config file. A requirements file without `pytest-cov` looks like a requirements file. An egress rule set to `0.0.0.0/0` is the most common four characters in any Terraform repo.

The same thing happened to me twice in two weeks. In Part 1 it was a retry wrapper raising its own exception type, so the dead-letter path never ran. Here it is a scanner with its rules switched off. Both passed review, mine included, because reading code tells you what it says and running it tells you what it does.

Running things has a catch of its own. The first Trivy count in this article came from an AI-assisted run on a copy of the repo that never downloaded the EKS module. It ran, it printed numbers, and the numbers were wrong. CI's clean checkout was the run that counted.

So the rule from Part 1 survives contact with Part 2: AI drafts, and the specification, the tests and the running system decide. I would add a line to it now. The output of a check is only evidence if you have seen the check fail, in the same place it will run for real.

## What I'd do differently

I would have built the pipeline first, before the Terraform, before the Helm charts, probably before the third service. Not because CI is more important than the code, but because for eight weeks I had five stages of YAML that described a quality bar and enforced none of it. That is worse than having no pipeline, because it reads like one.

I would also have protected `master` before the first workflow landed. The pull request that added the pipeline merged with its own checks red, because nothing required them to pass. A required status check is one setting.

The other thing is smaller and more annoying. `docs/lessons-learnt.md` and `docs/security.md` both say every AWS resource carries a `DataClass=patient-identifiable` tag through Terraform's provider-level `default_tags`, and describe the audit query that tag makes possible. The `default_tags` block in `versions.tf` is commented out. The documentation is describing a feature of the system I meant to build. Fixing that properly belongs with Part 5, on security and compliance, and it is going to be the theme of that article.

## Sources

- [HashiCorp: Dependency lock file](https://developer.hashicorp.com/terraform/language/files/dependency-lock)
- [HashiCorp: terraform validate](https://developer.hashicorp.com/terraform/cli/commands/validate)
- [HashiCorp: terraform fmt](https://developer.hashicorp.com/terraform/cli/commands/fmt)
- [Gitleaks](https://github.com/gitleaks/gitleaks)
- [Trivy: misconfiguration scanning](https://trivy.dev/latest/docs/scanner/misconfiguration/)
- [Trivy AVD-AWS-0104: unrestricted egress](https://avd.aquasec.com/misconfig/aws-0104)
- [Trivy AVD-AWS-0040: public EKS endpoint](https://avd.aquasec.com/misconfig/aws-0040)
- [Trivy: filtering and .trivyignore.yaml](https://trivy.dev/latest/docs/configuration/filtering/)
- [GitHub advisory for CVE-2024-47874 in Starlette](https://github.com/advisories/GHSA-f96h-pmfr-66vw)
- [GitHub: about protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)
- [Semgrep CLI reference](https://semgrep.dev/docs/cli-reference)
- [Anchore Syft](https://github.com/anchore/syft)
- [GitHub Actions: workflow syntax](https://docs.github.com/en/actions/writing-workflows/workflow-syntax-for-github-actions)
- [The code for this article, tag part-2](https://github.com/ND-codes/nhs-interop-platform/tree/part-2)

## Next

Part 3, on 4 November, is about the mapping itself: HL7 v2 ADT messages to FHIR R4, and the places it goes quietly wrong, several of them found by readers of Part 1.

What does your pipeline check that you have never watched fail?

---

This is a personal project. It is not affiliated with or endorsed by NHS England or any NHS organisation. It uses the public PDS FHIR sandbox and, earlier in the project, the PDS integration test environment, with synthetic test data only. Views are my own.
