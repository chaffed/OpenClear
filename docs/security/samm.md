# OWASP SAMM self-assessment

How mature OpenClear's security practices are, scored with [OWASP SAMM 2](https://owaspsamm.org/model/),
and where they should be in a year. Only the six practices a project with one maintainer can
act on are scored (OWASP_PLAN.md OW-D13); governance, training and organisational practices
score low by definition and aren't tracked here.

Scores run from 0 to 3 per stream: 1 is ad hoc but present, 2 is defined and repeatable, 3 is
measured and improved. A practice's score is the mean of its two streams.

**Assessed 2026-10-17. Re-score by 2027-10-17.**

| Practice | Stream | Today | Evidence today | In 12 months | What gets it there |
|---|---|---|---|---|---|
| **Threat assessment** (Design) | A. Application risk profile | 1.5 | [Risk profile and data classification](risk-profile.md) | 2 | Re-check the profile whenever a data class or outside service is added (PR checklist, OW5) |
| | B. Threat modeling | 1.5 | [Threat model as code](threat-model.md), checked by a test | 2 | Threat model updated in the same PR as a new trust boundary (PR checklist, OW5) |
| **Security requirements** (Design) | A. Software requirements | 2 | ASVS 5.0 target and assessment (ADR 0001); per-requirement marks on tests (OW-D4) | 2.5 | Crosswalk generated from test marks; new features name their ASVS requirements |
| | B. Supplier security | 0.5 | Outside services reviewed per bank (`outside_services.py`) | 1.5 | SBOM and dependency scanning (OW2); vendored JS tracked |
| **Secure build** (Implementation) | A. Build process | 1 | CI runs the suite on every push; images built from a clean copy | 2 | Lock files with hashes, Actions pinned to SHAs, base images by digest (OW2) |
| | B. Software dependencies | 0.5 | Lower bounds only | 2 | `pip-audit` gate, Dependabot, SBOM per release (OW2) |
| **Secure deployment** (Implementation) | A. Deployment process | 1 | Release steps written down (CLAUDE.md); sandbox deploy from a clean archive | 1.5 | Release metrics and SBOM in the steps (OW5, OW-D14) |
| | B. Secret management | 1.5 | Production refuses checked-in keys; secrets encrypted with a rotatable key; key-rotation control | 2 | Cryptography inventory and key policy ([#10](https://github.com/chaffed/OpenClear/issues/10)) |
| **Defect management** (Implementation) | A. Defect tracking | 1 | Private advisories and `security` issues, with GitHub's private reporting (ADR 0002) | 2 | Every finding triaged with a severity and a fix-by date from the remediation time frames ([#15](https://github.com/chaffed/OpenClear/issues/15)) |
| | B. Metrics and feedback | 0 | — | 1 | Release-time security metrics (OW-D14) |
| **Security testing** (Verification) | A. Scalable baseline | 1 | Security behaviour covered by unit and integration tests; row-level security tested on Postgres | 2 | CodeQL and Bandit (OW2), ZAP baseline on every PR (OW4) |
| | B. Deep understanding | 1 | [SECURITY_REVIEW.md](../../SECURITY_REVIEW.md); the ASVS assessment | 1.5 | ZAP active scans and the real-CRS edge check (OW4); an independent penetration test when a first client funds it |

| Practice | Today | In 12 months |
|---|---|---|
| Threat assessment | 1.5 | 2 |
| Security requirements | 1.25 | 2 |
| Secure build | 0.75 | 2 |
| Secure deployment | 1.25 | 1.75 |
| Defect management | 0.5 | 1.5 |
| Security testing | 1 | 1.75 |
