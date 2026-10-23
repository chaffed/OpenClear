# The CRI Profile

*Current as of 2026-10-22. Checked against its sources at least once a year (control check `regulatory_currency`).*

The Cyber Risk Institute's Profile is the financial sector's version of the NIST Cybersecurity
Framework 2.0: 318 *diagnostic statements* a bank answers with evidence, each mapped to the
regulations and guidance it satisfies. A bank answers the statements for its *impact tier*; most
community banks are Tier 4, with 208. When the FFIEC retired its Cybersecurity Assessment Tool on
31 August 2025, the agencies pointed banks to NIST CSF 2.0, CISA's Cybersecurity Performance
Goals, the CRI Profile and the CIS Controls. This page explains how to use OpenClear's
[CSF 2.0 crosswalk](csf.md) to answer the Profile. It isn't legal advice ([Compliance](README.md)).

## Using the CSF crosswalk

1. Each diagnostic statement is numbered from its CSF 2.0 subcategory: `GV.SC-05.01` sits under
   `GV.SC-05`. Find the subcategory in [csf.md](csf.md).
2. The controls listed there are what OpenClear does; the CUECs are what the bank must do for them
   to work. Cite both as evidence for the statements under that subcategory, with what the
   control's *Evidence* column points to (the Controls page, the evidence export).
3. The Profile adds an **Extend** function (third parties and dependencies) that has no CSF
   subcategory. Use [Third-party risk](tprm.md), [New York DFS Part 500](nydfs.md)'s section on
   service providers, and CUECs B-SA-9-01 and B-SA-9-02 for it.
4. CRI also publishes a mapping from the Profile to NIST SP 800-53 Rev. 5.1.1, the catalog
   OpenClear's controls and CUECs are filed under, so a bank can also go by 800-53 control id.

OpenClear's controls are evidence for the bank's answer, not the answer: a statement is the
bank's "Yes" only when the bank's part, the CUECs, is in place too.

A direct crosswalk from the Profile's statements to OpenClear's controls waits until a bank asks
for one (`plans/PARKED_PLAN.md`). The Profile is CRI's, licensed under Creative Commons
BY-NC-ND 4.0, so this page links to it rather than reproducing any of it; whether a table of
statement ids alone may be published is a question for CRI first.

## Sources

Read 22 October 2026: the CRI Profile v2.2, its Guidebook and its Impact Tiering Questionnaire
(Cyber Risk Institute, 27 April 2026; v2.2 leaves v2.1's statements unchanged), and the Profile
FAQ (licence), at [cyberriskinstitute.org](https://cyberriskinstitute.org/cri-profile-overview/);
the Federal Reserve's SR 24-7 and the FDIC's FIL-61-2024 (the Assessment Tool's retirement).
