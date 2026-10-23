# Regulatory plan: the law and guidance positive pay runs under

OpenClear has been mapped onto the security and IT-examination guidance a bank is examined
against: the FFIEC IT Handbook's security booklets, NIST SP 800-53 and CSF 2.0, OWASP ASVS 5.0
and SAMM, SR 11-7, the 2023 interagency third-party guidance, SOC 2 (through AICPA's mapping)
and the Nacha Operating Rules. What's missing is mostly the payments law positive pay exists
for (who bears a check-fraud loss, and the deadlines a return must meet), and several rules and
frameworks a bank's counsel, examiner or due-diligence team will raise. This plan evaluates 14
of them, then writes up the results in the order that matters most to a bank.

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

## The 14

| # | Rule, guidance or framework | Why it matters to positive pay | Priority |
|---|---|---|---|
| 1 | **UCC Articles 3 and 4** (as adopted by the states) | Who bears loss on forged and altered checks (3-406, 4-406 and the customer's duty to review), the payor bank's midnight deadline (4-301, 4-302), and how positive pay agreements shift loss | High |
| 2 | **Regulation CC** (12 CFR 229), Subpart C | Expeditious return and notice of nonpayment: the outer limit on when a returned check must move | High |
| 3 | **Check 21 Act and Regulation CC Subpart D; ECCHO Rules** | Substitute checks and their warranties; the image-exchange rules banks exchange X9.37 under, including return items and image quality | High |
| 4 | **Regulation E** (12 CFR 1005) | Consumer ACH: unauthorized-transfer claims, error resolution timelines, the WSUD's role | High |
| 5 | **Nacha's 2026 fraud-monitoring rules** | Originators and receiving banks monitoring for fraud, phased in during 2026; ACH positive pay as part of how a receiving bank meets it | High |
| 6 | **FFIEC IT Handbook: Retail Payment Systems; Business Continuity Management** | The booklet that covers check and ACH processing, and continuity at the cut-off | Medium |
| 7 | **FFIEC CAT's successor: the CRI Profile** (alongside CSF 2.0) | What community banks now assess against, as far as we know since the CAT's retirement | Medium |
| 8 | **GLBA: the Interagency Guidelines Establishing Information Security Standards** | The bank's duty to safeguard customer information, and to oversee service providers doing so | Medium |
| 9 | **State rules, chiefly NYDFS 23 NYCRR 500** | Often passed to vendors of New York-chartered banks: MFA, access reviews, incident notice | Medium |
| 10 | **FinCEN's check-fraud alert (2023) and SAR expectations** | How decisions, returns and declined teller verifications feed a bank's fraud reporting | Medium |
| 11 | **WCAG 2.2 AA; Section 508; the ADA** | A conformance statement or VPAT, asked for by larger banks | Low |
| 12 | **NIST AI RMF 1.0** (and its generative-AI profile, if relevant) | The usual companion to SR 11-7 for any model, even an advisory one | Low |
| 13 | **CFPB Section 1033** (personal financial data rights) | Whether any OpenClear API is consumer-data access; most likely not, with the reason written down | Low |
| 14 | **OFAC sanctions screening** | Whether payee screening is OpenClear's, the core's or the bank's | Low |

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
| 9 NYDFS Part 500 | Which sections a vendor is usually asked to meet (MFA, access privileges, audit trail, encryption, incident notice within 72 hours), and what OpenClear's controls and CUECs say for each |
| 10 FinCEN | What a bank needs to file a SAR for check fraud, and whether OpenClear's exports and the action log give it: decision history, declined teller verifications, the payee, images |
| 11 WCAG 2.2 | A conformance review of the key journeys against WCAG 2.2 AA: what the automated checks cover and what needs a person (already parked as the manual screen-reader pass) |
| 12 AI RMF | Govern, Map, Measure, Manage, against the advisory model's lifecycle: off by default, the bank's own data, replay, champion and challenger, the audit trail |
| 13 CFPB 1033 | Whether OpenClear is a "data provider" or holds "covered data" for consumers; the likely answer is no (business accounts, the bank's own vendor), recorded with reasons |
| 14 OFAC | Where payee screening happens in a bank's check flow; whether OpenClear's payee data or teller verification should note it; most likely a CUEC that it's the core's or the bank's |

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
- **RG-D4. Counsel reviews the check-law page before it's published as more than a draft.** It
  makes the claims a bank relies on most. Until then it carries "draft, for counsel's review".
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
| **RG3. Examination** | `ffiec.md` gains Retail Payment Systems and Business Continuity Management; `cri-profile.md` (or guidance on using CSF with it); `glba.md` | 6, 7, 8 | None expected: mappings onto existing controls, perhaps new CUECs | Examiners' questions, built on the controls RG1 and RG2 may add. The CRI Profile's licence is checked first. |
| **RG4. Vendor due diligence** | `nydfs.md`; FinCEN and SAR support in a section of `check-law.md` or its own page; OFAC as a CUEC with its reasoning | 9, 10, 14 | Maybe a decision-history export shaped for a SAR narrative | Asked for in a bank's vendor questionnaire, after the examiner's view is settled. |
| **RG5. Accessibility and AI** | An accessibility conformance statement (WCAG 2.2 AA) and a VPAT template; `ai-rmf.md` beside `sr-11-7.md`; the CFPB 1033 note | 11, 12, 13 | Fixes for any WCAG failures the review finds | Lower priority, and the manual accessibility pass is parked for a person; the statement can say what's automated. |
| **RG6. Keeping it current** | The `regulatory_currency` control and check; the yearly review in `docs/compliance/README.md` | all | The control | Last, once the pages it watches exist. |

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

## Questions for the product owner

- Whether a bank's counsel, or the project's own, will review the check-law page (RG-D4), and when.
- Whether the CRI Profile is the framework your target banks use, or another (for example, a
  state's or a bank association's).
- Whether any of your target banks are New York-chartered, which decides how much of RG4's NYDFS
  page is needed now.

## Progress log

- 2026-10-22: Plan written: 14 rules, guidance and frameworks to evaluate, how each is evaluated,
  and the stages RG1–RG6 in order. Nothing evaluated yet; the current texts are to be confirmed
  against their primary sources first (RG-D3).
