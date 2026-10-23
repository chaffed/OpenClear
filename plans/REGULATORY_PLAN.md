# Regulatory plan: the law and guidance positive pay runs under

OpenClear has been mapped onto the security and IT-examination guidance a bank is examined
against: the FFIEC IT Handbook's security booklets, NIST SP 800-53 and CSF 2.0, OWASP ASVS 5.0
and SAMM, SR 11-7, the 2023 interagency third-party guidance, SOC 2 (through AICPA's mapping)
and the Nacha Operating Rules. What's missing is mostly the payments law positive pay exists
for (who bears a check-fraud loss, and the deadlines a return must meet), and several rules and
frameworks a bank's counsel, examiner or due-diligence team will raise. This plan evaluates 15
of them, then writes up the results in the order that matters most to a bank.

**The banks this is for** (the product owner, 2026-10-22): state-chartered, with the FDIC as their
federal regulator. They're examined by the FDIC and their state banking department, jointly or in
turn, under the FFIEC IT Handbook; so FFIEC and FDIC rules come first. For state rules, OpenClear
**aims to meet New York DFS Part 500**, the most complete public state rule: what it asks of a
covered institution's third-party service provider, and the evidence a New York-regulated bank
needs for its own compliance.

**This isn't legal advice, and neither are the pages it produces.** Each page says what
OpenClear does and where the evidence is; each bank's counsel decides what its use of OpenClear
meets.

## What exists today

| Need | Today | Where |
|---|---|---|
| A page per framework | FFIEC (four booklets), Nacha, SR 11-7, third-party risk, ASVS, CSF 2.0, controls, CUECs, security metrics | `docs/compliance/` |
| Controls and CUECs, each with NIST 800-53 ids | 43 controls, most with live checks; 62 bank, customer and host CUECs | `compliance/controls.py`, `compliance/cuecs.py`, exported by `scripts/export_cuecs.py` |
| Crosswalks | NIST 800-53 to CSF 2.0 (NIST's mapping); SOC 2 loaded per installation | `compliance/crosswalks.py`, `scripts/import_crosswalks.py`, `docs/guides/grc.md` |
| Timing a return can depend on | The decision cut-off (bank-wide, per customer, business days, closures), default decisions, decisions locked and sent at the cut-off | `services/cutoff_service.py`, `services/auto_disposition_service.py`, `services/decision_outbox_service.py` |
| Check images and files | X9.37 import with control totals; returns as decision files, not X9.37 (parked) | `bulk_import/x937.py`; `plans/PARKED_PLAN.md` |
| Consumer ACH | The bank's own WSUD form; consumer SEC codes; returns with reasons | SIMPLIFY_PLAN.md SP1; `docs/compliance/nacha.md` |
| Accessibility | Automated checks on every key page, light and dark, phone to desktop | `tests/test_web/test_accessibility.py`, `test_viewports.py` |
| Advisory model | A bank's own model, off by default; model risk replay and anomaly capture | SIMPLIFY_PLAN.md SP2; `docs/compliance/sr-11-7.md` |

## The 15

| # | Rule, guidance or framework | Why it matters to positive pay | Priority |
|---|---|---|---|
| 1 | **UCC Articles 3 and 4** (as adopted by the states) | Who bears loss on forged and altered checks (3-406, 4-406 and the customer's duty to review), the payor bank's midnight deadline (4-301, 4-302), and how positive pay agreements shift loss | High |
| 2 | **Regulation CC** (12 CFR 229), Subpart C | Expeditious return and notice of nonpayment: the outer limit on when a returned check must move | High |
| 3 | **Check 21 Act and Regulation CC Subpart D; ECCHO Rules** | Substitute checks and their warranties; the image-exchange rules banks exchange X9.37 under, including return items and image quality | High |
| 4 | **Regulation E** (12 CFR 1005) | Consumer ACH: unauthorized-transfer claims, error resolution timelines, the WSUD's role | High |
| 5 | **Nacha's 2026 fraud-monitoring rules** | Originators and receiving banks monitoring for fraud, phased in during 2026; ACH positive pay as part of how a receiving bank meets it | High |
| 6 | **FFIEC IT Handbook: Retail Payment Systems; Business Continuity Management** | The booklet that covers check and ACH processing, and continuity at the cut-off | Medium |
| 7 | **The CRI Profile** (alongside CSF 2.0), the FFIEC CAT's successor in practice | What community banks now assess against since the CAT's retirement (see below). Decided: rely on the CSF 2.0 crosswalk now; a direct crosswalk waits for a bank that uses the Profile (RG-D6) | Low |
| 8 | **GLBA: the Interagency Guidelines Establishing Information Security Standards** | The bank's duty to safeguard customer information, and to oversee service providers doing so | Medium |
| 9 | **State rules: NYDFS 23 NYCRR 500 as the target; Massachusetts 201 CMR 17.00; CSBS tools** | NYDFS Part 500 is the most complete public state cybersecurity rule, and the one vendor questionnaires copy; OpenClear aims to meet it (RG-D7). Massachusetts 201 CMR 17.00 binds anyone holding Massachusetts residents' personal information, whatever the charter. The Conference of State Bank Supervisors publishes the tools state examiners share (such as the Ransomware Self-Assessment Tool) | High |
| 10 | **FinCEN's check-fraud alert (2023) and SAR expectations** | How decisions, returns and declined teller verifications feed a bank's fraud reporting | Medium |
| 11 | **WCAG 2.2 AA; Section 508; the ADA** | A conformance statement or VPAT, asked for by larger banks | Low |
| 12 | **NIST AI RMF 1.0** (and its generative-AI profile, if relevant) | The usual companion to SR 11-7 for any model, even an advisory one | Low |
| 13 | **CFPB Section 1033** (personal financial data rights) | Whether any OpenClear API is consumer-data access; most likely not, with the reason written down | Low |
| 14 | **OFAC sanctions screening** | Whether payee screening is OpenClear's, the core's or the bank's | Low |
| 15 | **The computer-security incident notification rule** (2021; for FDIC banks, 12 CFR Part 304 Subpart C); the **Bank Service Company Act** notice | Binds OpenClear itself: a bank service provider must notify its bank customers of a significant incident promptly. The banks must also notify the FDIC of the service relationship | High |

### The CRI Profile, defined

A cybersecurity assessment framework for financial institutions, maintained by the Cyber Risk
Institute, an industry non-profit (it began as the Financial Services Sector Cybersecurity
Profile). It builds on NIST's Cybersecurity Framework and turns it into **diagnostic
statements**, specific questions a bank answers with evidence about its controls, each mapped to
the regulations and guidance it satisfies (the FFIEC handbooks, the GLBA guidelines, NYDFS Part
500 and others), so one assessment answers several regulators. It scales by an **impact tier**,
so a small bank answers fewer statements than a large one. When the FFIEC retired its
Cybersecurity Assessment Tool in 2025, it pointed banks to NIST CSF 2.0 and to industry tools
like the Profile, and many community banks moved to it. A crosswalk from the Profile to
OpenClear's controls and CUECs lets a bank lift OpenClear's evidence straight into its own
assessment; the CSF 2.0 crosswalk already does much of the mapping. The current version (as far
as we know, 2.0, aligned to CSF 2.0) and its licence terms are confirmed before building on it.

## How each is evaluated

The same five steps for every one, so the results can be compared and audited.

1. **Confirm the current text.** Go to the primary source: the eCFR for Regulations CC and E;
   the Uniform Law Commission's official text and comments for the UCC, noting that states adopt
   it with variations; Nacha's rules and its published rule-change notices; the FFIEC IT Handbook
   InfoBase; the Cyber Risk Institute; the Federal Reserve, OCC and FDIC for the Interagency
   Guidelines; NYDFS; FinCEN; W3C; NIST; CFPB; OFAC. Record the version and date read in the page,
   and anything that changed since 2026 (the Nacha phase dates, the CRI Profile version, NYDFS
   amendments, the status of Section 1033). Secondary summaries are a pointer, never the source.
2. **List what it requires that touches positive pay.** Requirement by requirement, in a short
   table: what it requires, of whom (the bank, its customer, the service provider), and whether
   software can help at all. Most of these bind the bank, not OpenClear.
3. **Map each requirement** to one of:
   - **Met by OpenClear**: an existing control (with its id), or a feature, with its evidence.
   - **Supported**: OpenClear helps the bank meet it, and a CUEC says what the bank must do.
   - **The bank's alone**: nothing software does; a CUEC says so, so it isn't missed.
   - **A gap**: something OpenClear should do and doesn't. Each gap goes to the implementation
     stages below, with an estimate.
4. **Test what can be tested.** Where a requirement is about timing (the midnight deadline,
   Regulation CC's return), work examples through the cut-off service and add tests that pin the
   behaviour: a check presented on a Friday before a holiday, a bank in another time zone, a
   customer's own cut-off.
5. **Write it down** in the page's standard shape: what it is, what OpenClear does, the mapping
   table, the CUECs, the gaps, sources with dates, and "not legal advice".

### What each evaluation looks at

| # | The questions to answer |
|---|---|
| 1 UCC 3/4 | Can a decision cut-off be set later than the midnight deadline, and should OpenClear warn when it is? Does every check decision carry what a bank needs to show ordinary care (who, when, on what evidence)? Do the bank's positive pay agreements, which allocate loss under 3-406 and 4-406, have the facts they need (what was issued, what the customer saw, when it decided)? |
| 2 Reg CC | Do return decisions leave the bank in time for expeditious return? Is there anything for notice of nonpayment above the threshold, or is that the core's? What do the decision files and the feed carry that a return needs (return reason codes)? |
| 3 Check 21 / ECCHO | What does an X9.37 import assume about image quality and warranties? What would an X9.37 return file (parked) have to meet under ECCHO? Do substitute-check warranties change anything for an image-only presentment? |
| 4 Reg E | For a consumer debit returned as unauthorized: what timelines apply, what record of the claim the bank needs, and how the WSUD form and the return's timestamps support it? |
| 5 Nacha 2026 | What exactly the receiving-bank fraud-monitoring rule requires and from when, for which banks by volume; how ACH positive pay, authorizations and their audit trail count toward it; what a CUEC must say; does `nacha.md` need a new section? |
| 6 Retail Payment Systems; BCM | Each booklet section's examination objectives against OpenClear's controls, as the existing FFIEC page does; the cut-off outage CUEC against BCM |
| 7 CRI Profile | A crosswalk from the CRI Profile's diagnostic statements to OpenClear's controls and CUECs, the way `csf.md` maps CSF 2.0, if the Profile's licence allows publishing it; otherwise a page explaining how to use the CSF crosswalk with it |
| 8 GLBA Interagency Guidelines | Their elements (risk assessment, access controls, encryption, monitoring, service-provider oversight, incident response) against controls and the TPRM pack |
| 9 State rules | NYDFS Part 500 section by section (MFA, access privileges and reviews, audit trail, encryption, vendor management, incident notice): what OpenClear's controls and CUECs say for each, written as the benchmark for any state. Massachusetts 201 CMR 17.00's written-program and encryption requirements. Which CSBS tools a state examiner may use, and what they'd ask of OpenClear |
| 10 FinCEN | What a bank needs to file a SAR for check fraud, and whether OpenClear's exports and the action log give it: decision history, declined teller verifications, the payee, images |
| 11 WCAG 2.2 | A conformance review of the key journeys against WCAG 2.2 AA: what the automated checks cover and what needs a person (already parked as the manual screen-reader pass) |
| 12 AI RMF | Govern, Map, Measure, Manage, against the advisory model's lifecycle: off by default, the bank's own data, replay, champion and challenger, the audit trail |
| 13 CFPB 1033 | Whether OpenClear is a "data provider" or holds "covered data" for consumers; the likely answer is no (business accounts, the bank's own vendor), recorded with reasons |
| 14 OFAC | Where payee screening happens in a bank's check flow; whether OpenClear's payee data or teller verification should note it; most likely a CUEC that it's the core's or the bank's |
| 15 Incident notification | What counts as a notification incident for a bank service provider, how fast the bank customers must be told and how, and whether OpenClear's security events, SIEM forwarding and runbook support it; who OpenClear's operator notifies; the bank's own 36-hour notice to the FDIC, and its Bank Service Company Act notice of the relationship, as CUECs |

## Decisions

- **RG-D1. One page per subject, in `docs/compliance/`, linked from its README.** Check law
  (UCC 3/4, Regulation CC, Check 21 and ECCHO) is one page, `check-law.md`, as Nacha is one page
  for ACH; Regulation E joins `nacha.md` as a section, since it's about the same consumer debits.
  The rest each get their own page, or a section of an existing one where they're small (the
  FFIEC booklets go into `ffiec.md`).
- **RG-D2. Gaps become CUECs first, code second.** Most of these bind the bank. A gap is code
  only when OpenClear can actually meet the requirement for the bank; otherwise it's a CUEC with a
  check where one is possible.
- **RG-D3. Primary sources, dated.** Every page cites the primary source and the date it was
  read, and the progress log notes what changed. Anything we can't confirm from the source is
  marked as unconfirmed, not stated.
- **RG-D4. The check-law page is published as a draft until a bank's counsel reviews it.** It
  makes the claims a bank relies on most, so it carries "draft, for counsel's review" until then.
  The review waits on a bank (`plans/PARKED_PLAN.md`), and doesn't hold up the other stages.
- **RG-D6. The CRI Profile: the CSF 2.0 crosswalk now, a direct one when a bank asks.** The
  Profile is built on CSF 2.0, so a bank can get from `csf.md` to its Profile questions itself.
  `cri-profile.md` is a short page saying how. A direct crosswalk (and checking the Profile's
  licence lets it be published) waits for a bank that assesses with it (`plans/PARKED_PLAN.md`).
- **RG-D7. NYDFS Part 500 is a target, not a reference.** OpenClear meets, as a third-party
  service provider, what Part 500 lets a covered bank require of one (its 500.11 policy: access
  controls and MFA, encryption, notice of a cybersecurity event affecting the bank, and the
  bank's right to assess), and gives a covered bank evidence for each section that touches
  OpenClear. Where OpenClear falls short, the gap is built, not just recorded; where only the bank
  can meet a section, a CUEC says so. A New York-regulated bank's own Part 500 obligations
  (its CISO, its annual certification) stay its own.
- **RG-D5. A yearly review.** Each page names when it was last checked against its source; the
  control `regulatory_currency` warns when any page is more than a year old (like
  `software_currency`).

## Stages, in order

Ordered by what a bank's counsel and examiner raise first, then by how much each depends on the
one before. Each stage is a commit (or a PR) with its pages, CUECs, controls, tests and the
crosswalk exports regenerated.

| Stage | What | Covers | Likely code | Why this order |
|---|---|---|---|---|
| **RG1. Check law** | `check-law.md`: UCC 3/4, Regulation CC, Check 21 and ECCHO, evaluated and mapped; timing tests through the cut-off service | 1, 2, 3 | A warning when a decision cut-off is set later than the bank's return deadline allows (likely, once confirmed); a CUEC for the positive pay agreement's loss allocation | It's why positive pay exists, and the first thing counsel asks. The cut-off checks it needs inform RG2. |
| **RG2. ACH: Regulation E and Nacha 2026** | A Regulation E section in `nacha.md`; the 2026 fraud-monitoring rules evaluated, with the receiving-bank side mapped to ACH positive pay | 4, 5 | Possibly evidence a bank can export for its fraud-monitoring program (authorizations, unauthorized-debit returns and their timing) | The 2026 rules are in force now, so banks are asking. Regulation E shares the consumer-debit facts. |
| **RG3. New York DFS Part 500 and incident notice** | `nydfs.md`: Part 500 section by section, each met by OpenClear, supported (with a CUEC) or the bank's alone, and the third-party service provider requirements met (RG-D7); `incident-notification.md` for the FDIC rule and Part 500's event notice together | 9 (NYDFS), 15 | Whatever gaps Part 500 shows (likely: how the operator notifies each bank of an incident, in the app and by email, with a runbook; possibly access-review and MFA evidence shaped for 500.7 and 500.12) | The product owner's target, and Part 500's vendor requirements and the FDIC notification rule overlap, so one stage builds both. |
| **RG4. Examination** | `ffiec.md` gains Retail Payment Systems and Business Continuity Management; `glba.md` (with FDIC Part 364); `cri-profile.md`, the short page on using the CSF crosswalk (RG-D6) | 6, 7, 8 | None expected: mappings onto existing controls, perhaps new CUECs | Examiners' questions for FDIC banks, mostly mapping what RG1–RG3 built. |
| **RG5. Vendor due diligence** | `state-rules.md`: Massachusetts 201 CMR 17.00 and the CSBS tools, beside the NYDFS page; FinCEN and SAR support; OFAC as a CUEC with its reasoning | 9 (others), 10, 14 | Maybe a decision-history export shaped for a SAR narrative | Asked for in a bank's vendor questionnaire, after the examiner's view is settled. |
| **RG6. Accessibility and AI** | An accessibility conformance statement (WCAG 2.2 AA) and a VPAT template; `ai-rmf.md` beside `sr-11-7.md`; the CFPB 1033 note | 11, 12, 13 | Fixes for any WCAG failures the review finds | Lower priority, and the manual accessibility pass is parked for a person; the statement can say what's automated. |
| **RG7. Keeping it current** | The `regulatory_currency` control and check; the yearly review in `docs/compliance/README.md` | all | The control | Last, once the pages it watches exist. |

Each stage's evaluation (the five steps) comes before its writing: RG1 starts by confirming the
current UCC, Regulation CC and ECCHO texts, and its gaps decide its code.

## How each stage is built

1. **Research** (a few hours per subject): read the primary sources in step 1, and fill in the
   evaluation questions above in a working note in the PR.
2. **Map and decide**: the requirement table, each row met, supported, the bank's alone, or a
   gap. Gaps are listed in the PR with what fixing them would take; the product owner decides
   which are built in the stage and which are parked.
3. **Build**: the page; new CUECs and controls in `compliance/cuecs.py` and
   `compliance/controls.py` (with checks in `services/control_checks.py` where they can be
   automated); any code the decided gaps need, with tests; `scripts/export_cuecs.py` re-run;
   `docs/compliance/README.md` linked; the CHANGELOG under "Unreleased".
4. **Check**: the compliance and docs tests; the full suites when there's code; for RG1, the page
   marked as a draft for counsel (RG-D4).

## Questions answered

- **Counsel's review of the check-law page** waits on a bank (`plans/PARKED_PLAN.md`); the page
  is published as a draft until then (RG-D4).
- **The target banks** are state-chartered, with the FDIC as their federal regulator. State rules
  use NYDFS Part 500 as the benchmark, with Massachusetts 201 CMR 17.00 and the CSBS tools.
- **The CRI Profile** is defined above. Decided: the CSF 2.0 crosswalk now, a direct one when a
  bank asks (RG-D6).
- **New York DFS Part 500** is the target for state rules, not just a reference (RG-D7), and moves
  up to RG3.

## Progress log

- 2026-10-22: Plan written: 14 rules, guidance and frameworks to evaluate, how each is evaluated,
  and the stages RG1–RG6 in order.
- 2026-10-22: The product owner's answers: counsel's review is parked until a bank; the banks are
  state-chartered and FDIC-supervised, so state rules take NYDFS Part 500 as the benchmark, with
  Massachusetts 201 CMR 17.00 and the CSBS tools; the CRI Profile is defined. Added item 15, the
  computer-security incident notification rule, which binds OpenClear as a bank service provider,
  in RG3. Nothing evaluated yet; the current texts are to be confirmed
  against their primary sources first (RG-D3).
- 2026-10-22: The product owner set New York DFS Part 500 as the target (RG-D7); it moves to RG3
  with incident notification, and examination becomes RG4. The CRI Profile: the CSF crosswalk now,
  a direct crosswalk when a bank asks (RG-D6). Stages are now RG1–RG7.

