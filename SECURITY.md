# Security policy

## Reporting a vulnerability

Report it privately through GitHub:
**[Report a vulnerability](https://github.com/chaffed/OpenClear/security/advisories/new)**.
Please don't open a public issue, pull request or discussion about it.

If you're a bank or a bank's customer using OpenClear, tell the service provider you get it
from first: they can protect their installation while the fix is made, and they'll pass it on.
Many installations publish their own contact at `/.well-known/security.txt`.

Include what you can of:
- the version (shown in `/openapi.json`) and how it's deployed;
- the steps to reproduce, and what an attacker gains;
- whether you've seen it used against a real installation.

## What happens next

| Step | Target |
|---|---|
| We acknowledge your report | 5 business days |
| We confirm whether it's a vulnerability, and its severity (CVSS) | 10 business days |
| A fixed release, for a critical vulnerability | 7 days from confirmation |
| ... high | 30 days |
| ... medium | 90 days |
| ... low | The next planned release |

We'll keep you told as the fix progresses, and agree a disclosure date with you. Once a fixed
release is out we publish a GitHub security advisory (with a CVE where it applies) and credit
you, unless you'd rather not be named. Until then, the details stay private
([ADR 0002](docs/adr/0002-security-gaps-stay-private-until-fixed.md)).

## Supported versions

Security fixes go into the latest release; upgrade to it. Upgrading is always supported (the
suite builds the database from nothing to the latest migration), and going back up to two
minor versions is tested too, in case an upgrade has to be undone.

## Scope

In scope: OpenClear's server (`src/openclear`), its Docker image, and the SDKs in `sdks/`.

Out of scope:
- **How an installation is run:** its edge, TLS, hosting, keys and settings. Report those to
  its service provider.
- **Third-party plugins.** Report those to their authors.
- **The public sandbox at openclear-sandbox.fly.dev:** reports about OpenClear found there are
  welcome, but don't run automated scanners against it, attempt denial of service, or touch
  data that isn't yours.

## Safe harbor

We won't take legal action against good-faith research that follows this policy: it avoids
harming people's data and service, stays within your own accounts and test data, and gives us
reasonable time to fix the problem before anything is made public.

## How OpenClear approaches security

See the [risk profile](docs/security/risk-profile.md), the [threat model](docs/security/threat-model.md),
and [OWASP_PLAN.md](plans/OWASP_PLAN.md) for the ASVS 5.0 target and how it's checked.
