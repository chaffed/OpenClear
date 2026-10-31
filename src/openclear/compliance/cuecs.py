# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Complementary user entity controls (CUECs; CONTROLS_PLAN.md CT0): what OpenClear's
controls rely on the people using it to do. Each CUEC is filed under the NIST SP 800-53
Rev. 5 control it carries out, so a bank or a business customer can map it onto its own
control inventory and attest to it, by hand or automatically from OpenClear's tests (CT3).

Three parties:
- `bank`: the bank, for its own staff, configuration and processes;
- `customer`: a bank's business customer, for its own people and its part of positive pay;
- `host`: whoever runs OpenClear. That's the bank when it hosts OpenClear itself, or its
  hosting provider, for whom these are the controls the bank relies on it for.

An id is `<party letter>-<NIST control>-<nn>` (B-IA-2(1)-01). Ids are stable: a CUEC that's
withdrawn is marked so and its id is never reused. `scripts/export_cuecs.py` writes the
published copies (docs/compliance/cuecs.md, .json, .csv) from this module."""

import re
from dataclasses import dataclass, field

from openclear.compliance.crosswalks import csf_for, csf_title, soc2_for

VERSION = "1.0"
NIST_CATALOG = "NIST SP 800-53 Rev. 5.1.1"

PARTIES = {
    "bank": "The bank",
    "customer": "The business customer",
    "host": "Whoever runs OpenClear: the bank when it hosts OpenClear itself, or its hosting provider",
}
PARTY_LETTERS = {"bank": "B", "customer": "C", "host": "H"}

# How OpenClear can evidence a CUEC (CONTROLS_PLAN.md CT-D5):
EVIDENCE = {
    "automated": "OpenClear's own data shows whether it operates; a test can attest to it for a period",
    "assisted": "OpenClear supplies the evidence (a report, an export, a recorded review); a person judges it",
    "attested": "outside what OpenClear can see; a person attests, and may attach evidence",
}

# The controls and enhancements the CUEC and control inventories use (compliance/controls.py),
# with their Rev. 5 titles. Every id either names must be here, so a typo can't reach a bank's
# mapping.
NIST_TITLES = {
    "AC-2": "Account Management",
    "AC-2(1)": "Automated System Account Management",
    "AC-2(3)": "Disable Accounts",
    "AC-3": "Access Enforcement",
    "AC-3(2)": "Dual Authorization",
    "AC-4": "Information Flow Enforcement",
    "AC-5": "Separation of Duties",
    "AC-6": "Least Privilege",
    "AC-6(5)": "Privileged Accounts",
    "AC-6(7)": "Review of User Privileges",
    "AC-7": "Unsuccessful Logon Attempts",
    "AC-12": "Session Termination",
    "AC-21": "Information Sharing",
    "PL-1": "Policy and Procedures",
    "SA-11": "Developer Testing and Evaluation",
    "CA-8": "Penetration Testing",
    "MP-6": "Media Sanitization",
    "AT-2": "Literacy Training and Awareness",
    "AT-2(3)": "Social Engineering and Mining",
    "AT-3": "Role-based Training",
    "AU-2": "Event Logging",
    "AU-3": "Content of Audit Records",
    "AU-6": "Audit Record Review, Analysis, and Reporting",
    "AU-6(1)": "Automated Process Integration",
    "AU-7": "Audit Record Reduction and Report Generation",
    "AU-9": "Protection of Audit Information",
    "AU-9(2)": "Store on Separate Physical Systems or Components",
    "AU-9(3)": "Cryptographic Protection",
    "AU-10": "Non-repudiation",
    "AU-11": "Audit Record Retention",
    "AU-12": "Audit Record Generation",
    "CA-2": "Control Assessments",
    "CA-3": "Information Exchange",
    "CA-7": "Continuous Monitoring",
    "CM-2": "Baseline Configuration",
    "CM-3": "Configuration Change Control",
    "CM-4": "Impact Analyses",
    "CM-5": "Access Restrictions for Change",
    "CM-6": "Configuration Settings",
    "CM-8": "System Component Inventory",
    "CP-2": "Contingency Plan",
    "CP-4": "Contingency Plan Testing",
    "CP-9": "System Backup",
    "CP-10": "System Recovery and Reconstitution",
    "IA-2": "Identification and Authentication (Organizational Users)",
    "IA-2(1)": "Multi-factor Authentication to Privileged Accounts",
    "IA-2(2)": "Multi-factor Authentication to Non-privileged Accounts",
    "IA-2(8)": "Access to Accounts — Replay Resistant",
    "IA-5": "Authenticator Management",
    "IA-5(1)": "Password-based Authentication",
    "IA-8": "Identification and Authentication (Non-organizational Users)",
    "IA-9": "Service Identification and Authentication",
    "IA-12": "Identity Proofing",
    "IR-4": "Incident Handling",
    "IR-6": "Incident Reporting",
    "IR-8": "Incident Response Plan",
    "MA-4": "Nonlocal Maintenance",
    "MP-4": "Media Storage",
    "PS-4": "Personnel Termination",
    "PS-5": "Personnel Transfer",
    "RA-3": "Risk Assessment",
    "RA-5": "Vulnerability Monitoring and Scanning",
    "SA-9": "External System Services",
    "SA-4": "Acquisition Process",
    "SC-5": "Denial-of-service Protection",
    "SC-7": "Boundary Protection",
    "SC-8": "Transmission Confidentiality and Integrity",
    "SC-8(1)": "Cryptographic Protection",
    "SC-12": "Cryptographic Key Establishment and Management",
    "SC-13": "Cryptographic Protection",
    "SC-18": "Mobile Code",
    "SC-23": "Session Authenticity",
    "SC-28": "Protection of Information at Rest",
    "SC-28(1)": "Cryptographic Protection",
    "SI-2": "Flaw Remediation",
    "SI-3": "Malicious Code Protection",
    "SI-4": "System Monitoring",
    "SI-7": "Software, Firmware, and Information Integrity",
    "SI-7(6)": "Cryptographic Protection",
    "SI-10": "Information Input Validation",
    "SI-12": "Information Management and Retention",
    "PT-2": "Authority to Process Personally Identifiable Information",
    "PS-3": "Personnel Screening",
    "SR-6": "Supplier Assessments and Reviews",
}
NIST_ID = re.compile(r"^(AC|AT|AU|CA|CM|CP|IA|IR|MA|MP|PE|PL|PM|PS|PT|RA|SA|SC|SI|SR)-\d{1,2}(\(\d{1,2}\))?$")

# The live control checks (compliance/checks.py) a CUEC's evidence can come from.
from openclear.compliance.checks import KEYS as _LIVE  # noqa: E402

EXISTING_CHECKS = frozenset(_LIVE)
# Checks still to come, each for the CUECs that name it ({key: what it shows}); none now.
PLANNED_CHECKS: dict[str, str] = {}


@dataclass(frozen=True)
class Cuec:
    id: str
    party: str
    nist: str  # the 800-53 control this CUEC carries out
    title: str
    statement: str  # what the party does
    complements: str  # what OpenClear does that relies on it
    evidence: str  # automated | assisted | attested
    check: str | None  # the test that evidences it (EXISTING_CHECKS or PLANNED_CHECKS)
    frequency: str
    where: str  # where the evidence is in OpenClear
    related: tuple[str, ...] = field(default=())
    withdrawn: bool = False

    @property
    def nist_title(self) -> str:
        return NIST_TITLES[self.nist]

    @property
    def csf(self) -> list[dict]:
        """The NIST CSF 2.0 subcategories NIST maps to its 800-53 control (crosswalks.py)."""
        return [{"id": k, "title": csf_title(k)} for k in csf_for(self.nist)]

    @property
    def oscal_id(self) -> str:
        """The control's id in NIST's OSCAL catalog: AC-2(1) is `ac-2.1`."""
        return oscal_id(self.nist)

    @property
    def check_status(self) -> str | None:
        if self.check is None:
            return None
        return "live" if self.check in EXISTING_CHECKS else "planned"

    def as_dict(self) -> dict:
        return {
            "id": self.id, "party": self.party, "nist": self.nist, "nist_title": self.nist_title, "oscal_id": self.oscal_id,
            "related": list(self.related), "title": self.title, "statement": self.statement, "complements": self.complements,
            "evidence": self.evidence, "check": self.check, "check_status": self.check_status, "frequency": self.frequency,
            "csf": self.csf,
            "soc2": None if (found := soc2_for(self.nist)) is None else list(found),
            "where": self.where, "withdrawn": self.withdrawn,
        }


def oscal_id(nist: str) -> str:
    return re.sub(r"\((\d+)\)", r".\1", nist).lower()


CUECS: tuple[Cuec, ...] = (
    # --- The bank: its staff -------------------------------------------------------------
    Cuec("B-AC-2-01", "bank", "AC-2", "Provision and remove staff",
         "Approve each staff member's access before adding them, change it when their job changes, and deactivate them in "
         "OpenClear (or in the bank's identity provider) by the bank's own deadline when they leave.",
         "OpenClear grants only what a person's security group allows, records every membership change in the action log, "
         "and stops a deactivated person signing in or using a token at once.",
         "assisted", "dormant_accounts", "Per event; reviewed quarterly",
         "Users → Export (with last sign-in); user.* and membership.* in the action log",
         related=("AC-2(3)", "PS-4", "PS-5")),
    Cuec("B-AC-2(1)-01", "bank", "AC-2(1)", "Keep the identity provider in step",
         "When staff sign on through the bank's identity provider, keep its group mapping and SCIM provisioning in step with "
         "the bank's directory, so someone disabled there is disabled in OpenClear.",
         "OpenClear applies SCIM deactivations and group changes as they arrive, and refuses a sign-on the identity provider refuses.",
         "assisted", "scim_sync", "Continuous; reviewed quarterly",
         "Settings → Sign-on; scim.* in the action log", related=("AC-2", "PS-4")),
    Cuec("B-AC-6(7)-01", "bank", "AC-6(7)", "Review staff access",
         "At least quarterly, review each staff member's security group, customer scope and account access, remove what's no "
         "longer needed, and record the review.",
         "OpenClear exports every membership with its group, scope, account access and last sign-in, and records each change.",
         "assisted", "access_review", "Quarterly",
         "Users → Export; the access review record (CT3)", related=("AC-2", "AC-6")),
    Cuec("B-AC-6-01", "bank", "AC-6", "Give the least access a job needs",
         "Decide who holds admin permissions and keep them few; use customer scope and per-account access where a role is "
         "narrower than the whole bank; don't give day-to-day deciders admin permissions.",
         "OpenClear checks every request against the person's permissions, scope and account access.",
         "assisted", "privileged_accounts", "Quarterly",
         "Settings → Security groups; Users → Export", related=("AC-6(5)", "AC-2")),
    Cuec("B-AC-5-01", "bank", "AC-5", "Separate conflicting duties",
         "Design security groups so no group combines conflicting duties, then move segregation of duties from warn to enforce.",
         "OpenClear checks groups against the bank's conflict rules, refuses a breach under enforce (ERR_SOD_VIOLATION) and logs "
         "it under either mode, and never lets whoever presented an item decide it.",
         "automated", "segregation_of_duties", "Continuous",
         "Settings → Segregation of duties; Controls; sod.violation in the action log", related=("AC-6",)),
    Cuec("B-AC-3(2)-01", "bank", "AC-3(2)", "Use dual control on decisions",
         "Turn on dual control for pay/return decisions where the bank's policy needs a second person, and staff it so a checker "
         "is available before every cutoff.",
         "OpenClear holds a recommended decision until a different person approves it, and records both.",
         "automated", "dual_control", "Continuous",
         "Settings → Decisions; decision histories in the evidence export", related=("AC-5",)),
    Cuec("B-CM-3-01", "bank", "CM-3", "Approve rule and model changes",
         "Have a second admin approve every change to matching rules and scoring models, and approve only changes the bank has assessed.",
         "Under enforce, OpenClear holds a rule or model change until another admin approves it, and records both people.",
         "automated", "two_person_changes", "Per change",
         "Settings → Changes; the evidence export's changes", related=("AC-3(2)", "CM-5")),
    Cuec("B-CM-4-01", "bank", "CM-4", "Replay before changing rules",
         "Before approving a matching-rule or model change, replay recent presented-item files through it at Model risk and review "
         "the variance report.",
         "OpenClear's replay runs the real matching code in a dry run that's rolled back, and keeps the report.",
         "automated", "change_impact_replay", "Per change",
         "Settings → Model risk; control runs in the evidence export", related=("CA-2", "CM-3")),
    Cuec("B-IA-2(1)-01", "bank", "IA-2(1)", "Second factor for admins",
         "Make everyone who can change who may do what sign in with a security key or passkey, or only through the bank's "
         "identity provider with a second factor there.",
         "OpenClear supports FIDO2 security keys and passkeys, and SSO-only sign-in per bank, and reports admins without either.",
         "automated", "admin_mfa", "Continuous",
         "Controls → Admins use a second factor", related=("IA-2", "AC-6(5)")),
    Cuec("B-IA-2(2)-01", "bank", "IA-2(2)", "Second factor for staff",
         "Require a second factor (a security key or passkey, or the bank's identity provider) of everyone who can decide "
         "exceptions or change data; a bank under New York DFS Part 500 (500.12, from November 2025) requires it of every person "
         "who signs in.",
         "OpenClear supports FIDO2 security keys and passkeys, and SSO-only sign-in per bank.",
         "automated", "staff_mfa", "Continuous",
         "Users → Export; Settings → Sign-on", related=("IA-2",)),
    Cuec("B-IA-2(2)-02", "bank", "IA-2(2)", "Second factor at the identity provider",
         "Have the bank's identity provider (and any customer's the bank allows) require a second factor for everyone who "
         "signs in to OpenClear through it.",
         "People who sign in through single sign-on are exempt from OpenClear's own security-key requirement, which relies on "
         "this; a connection can be set to refuse a sign-in the provider doesn't report as multi-factor.",
         "assisted", None, "Yearly", "Settings → Sign-on (Require the identity provider to report MFA)", related=("IA-2", "IA-8")),
    Cuec("B-IA-2(2)-03", "bank", "IA-2(2)", "Second factor in online banking",
         "Have the online banking site that shows OpenClear inside its pages sign its users in with a second factor.",
         "An embedded session is started by the online banking site for a user it has already signed in, so it's exempt from "
         "OpenClear's own security-key requirement.",
         "attested", None, "Yearly", "Settings → Embedding in online banking", related=("IA-2", "IA-8")),
    Cuec("B-IA-5(1)-01", "bank", "IA-5(1)", "Set the password policy",
         "When password sign-in is on, set the password policy (length, character rules, lockout) to the bank's own standard.",
         "OpenClear enforces the bank's password policy and locks an account after repeated failures.",
         "automated", "password_policy", "On change; reviewed yearly",
         "Settings → Password policy; tenant.* in the action log", related=("IA-5", "AC-7")),
    Cuec("B-IA-5-01", "bank", "IA-5", "Look after integration credentials",
         "Rotate API client secrets at least yearly (or use key pairs), keep them in the bank's secret store, and revoke a client "
         "when its system is retired.",
         "OpenClear shows each secret only once, stores only its hash, supports private_key_jwt, and reports secrets over a year old.",
         "automated", "api_client_secrets", "Continuous",
         "Settings → API clients; Controls → Integration secrets rotated", related=("IA-9",)),
    Cuec("B-MA-4-01", "bank", "MA-4", "Grant support access with care",
         "Grant the platform's support staff access only for a stated reason and period, approve write access only when it's "
         "needed, and review each grant.",
         "OpenClear gives support staff no access to a bank except what the bank grants, under their own name, ending on time.",
         "automated", "support_access_review", "Per event",
         "Settings → Support access; support_access.* in the action log", related=("AC-2",)),
    # --- The bank: its customers ---------------------------------------------------------
    Cuec("B-IA-12-01", "bank", "IA-12", "Verify customers' administrators",
         "Verify who a business customer's administrators are, and their authority to act for the customer, before giving them "
         "access or letting the customer manage its own people.",
         "OpenClear gives a customer's people access only through the bank, or through administrators the bank has set up.",
         "attested", None, "Per event", "Customers → People; customer.* in the action log", related=("AC-2",)),
    Cuec("B-AC-2-02", "bank", "AC-2", "Let customers manage themselves with care",
         "Let a customer manage its own people or sign-on only after verifying its administrators, and make sure each customer "
         "that manages its own people has one.",
         "OpenClear limits a self-managing customer to its own people, accounts and the roles the bank offers.",
         "automated", "customer_self_service", "Continuous",
         "Controls → Customer self-service", related=("IA-12",)),
    Cuec("B-IA-8-01", "bank", "IA-8", "Set how customers sign in",
         "Decide how each business customer's people must sign in (password policy, second factor, the customer's own identity "
         "provider) and set it.",
         "OpenClear enforces each customer's password policy, second factors and sign-on connection.",
         "automated", "customer_mfa", "On change; reviewed yearly",
         "Customers → Sign-on; Customers → Password policy", related=("IA-2(2)",)),
    Cuec("B-PL-1-01", "bank", "PL-1", "Cover OpenClear in written policies",
         "Cover positive pay in the bank's cybersecurity policies (vendor management, incident response and notification, "
         "access control) and its written information security program, and approve them each year (23 NYCRR 500.3; for "
         "Massachusetts residents' information, 201 CMR 17.03).",
         "OpenClear's controls and CUECs say what it does and what it relies on the bank for.",
         "attested", None, "Yearly", "docs/compliance/controls.md; docs/compliance/cuecs.md"),
    Cuec("B-CA-2-01", "bank", "CA-2", "Support the annual certification",
         "Keep the evidence exports and host attestations that support the bank's annual cybersecurity certification (for New York "
         "DFS, by April 15, 23 NYCRR 500.17(b)) for five years.",
         "OpenClear's evidence export is signed, and its host attestations are recorded.",
         "attested", None, "Yearly", "Settings → Controls → Export evidence"),
    Cuec("B-CM-8-01", "bank", "CM-8", "Inventory OpenClear",
         "List OpenClear and its host in the bank's asset inventory, with an owner, the data's classification and a recovery time "
         "before the decision cut-off (23 NYCRR 500.13).",
         "OpenClear lists the outside services turned on, and the evidence export records the version running.",
         "attested", None, "Yearly", "Settings → Controls; docs/compliance/tprm.md"),
    Cuec("B-RA-3-02", "bank", "RA-3", "Know which accounts are consumer accounts",
         "Identify each account on OpenClear that is a consumer account under Regulation E, and apply the bank's Regulation E "
         "procedures to it alongside positive pay, and meet any duty under the CFPB's personal financial data rights rule "
         "(12 CFR 1033) for it through the bank's own interfaces.",
         "OpenClear protects business and consumer accounts the same way, and its decision history shows when an item was "
         "shown, decided and by whom.",
         "attested", None, "Yearly", "Customers → Accounts; docs/compliance/nacha.md (Regulation E)"),
    Cuec("B-RA-3-01", "bank", "RA-3", "Assess positive pay risk",
         "Include OpenClear in the bank's risk assessment and in the information security program's yearly report to the board "
         "(12 CFR 364 App. B III.F), and set each customer's services and default dispositions from it.",
         "OpenClear applies each customer's services and default dispositions as set.",
         "attested", None, "Yearly", "Customers → Positive pay settings"),
    Cuec("B-RA-3-03", "bank", "RA-3", "Keep the check-fraud controls positive pay doesn't provide",
         "Keep account opening and monitoring, kiting detection and stop payments in the bank's own program and the core.",
         "OpenClear catches checks that weren't issued, altered amounts and payees, duplicates and a check cashed at a branch, but "
         "sees nothing of how an account was opened or of money moving between accounts.",
         "attested", None, "Yearly", "The bank's fraud program"),
    Cuec("B-SI-12-02", "bank", "SI-12", "Offboard customers that leave",
         "When a customer leaves the bank or stops positive pay, set its last service day in OpenClear, stop the core sending its "
         "items, and keep the records the bank must keep in the core, which is the system of record.",
         "OpenClear ends the customer's sign-in and refuses its accounts' items after the last service day, and deletes its "
         "data 90 days later with a signed certificate, unless it's on legal hold.",
         "attested", None, "Per event", "Customers → a customer's Offboarding; Customers → Deleted customers", related=("AC-2",)),
    Cuec("B-SI-12-03", "bank", "SI-12", "Keep custom fields to what positive pay needs",
         "Define custom fields on customers and accounts only for what the service needs (identifiers, codes), never a "
         "taxpayer id, a password or personal data with no use here; mark a field shown to the customer only when the "
         "customer should see it; and review the fields yearly.",
         "OpenClear limits fields to 25 per kind, records every definition and value change with before and after, shows a "
         "customer only fields marked for it, says what not to store on the page where fields are defined, and warns while a "
         "required field is empty.",
         "attested", None, "Yearly", "Settings → Custom fields; the action log", related=("PT-2", "AC-3")),
    Cuec("B-RA-3-04", "bank", "RA-3", "Screen for sanctions where the money moves",
         "Screen accountholders, people cashing checks, payees where the bank's risk assessment calls for it, and inbound IAT "
         "parties for OFAC sanctions in the core, teller and ACH systems. A pay decision in OpenClear isn't an OFAC clearance.",
         "OpenClear doesn't screen for sanctions. It shows the payee as issued, as read from the image and as typed at a branch, "
         "so a bank can check one when it has reason to.",
         "attested", None, "Yearly", "docs/compliance/fincen-ofac.md", related=("SI-4",)),
    Cuec("B-SC-28-01", "bank", "SC-28", "Protect downloads on the bank's devices",
         "Encrypt laptops and portable media that hold OpenClear exports, decision files or check images, and keep such files off "
         "unmanaged devices (201 CMR 17.04(5)).",
         "OpenClear records each export and download in the action log.",
         "attested", None, "Yearly", "The bank's device management", related=("MP-4",)),
    Cuec("B-RA-3-05", "bank", "RA-3", "Govern the scoring model",
         "Before turning fraud scoring on, inventory the bank's model under its model-risk or AI policy, tier it and name an owner; "
         "while it's on, review its metrics, champion/challenger record and replays at least yearly, and turn it off if they "
         "degrade.",
         "OpenClear keeps scoring off until the bank turns it on, activates a model only after 200 of the bank's own decisions, "
         "learns only from decisions people made, and shows each model's metrics and how it was chosen.",
         "attested", None, "Yearly", "Settings → ML models; docs/compliance/ai-rmf.md", related=("CA-7", "CM-4")),
    Cuec("B-SI-12-01", "bank", "SI-12", "Set retention and disposal",
         "Decide how long positive pay records (issued checks, items, images, decisions) are kept and how they're disposed of, and "
         "at exit obtain the host's record that the bank's data was deleted (12 CFR 364 App. B II.B.4, III.C.4).",
         "OpenClear keeps a bank's business records until the bank leaves; then, after the bank is told and a wait, it deletes "
         "them and signs a certificate whose action-log hash lets the bank check its final export is complete.",
         "attested", None, "Yearly, and at exit", "Settings → Data export; the deletion certificate", related=("MP-6",)),
    # --- The bank: monitoring ------------------------------------------------------------
    Cuec("B-CA-7-01", "bank", "CA-7", "Act on control status",
         "Watch control status (the Controls page, the API, or control.status.changed in the SIEM) and investigate a WARN or FAIL "
         "by the bank's own deadline.",
         "OpenClear tests its controls hourly and raises a security event when one changes.",
         "automated", "control_response", "Continuous",
         "Settings → Controls; control.status.changed security events", related=("SI-4",)),
    Cuec("B-CA-7-02", "bank", "CA-7", "Run the anomaly-capture test",
         "Turn on the daily anomaly-capture test, and investigate any synthetic fraud it misses.",
         "OpenClear's anomaly-capture test runs injected fraud through the bank's live rules in a dry run and records what was caught.",
         "automated", "anomaly_capture", "Daily",
         "Settings → Model risk; Controls → Synthetic fraud caught", related=("CA-2", "SI-4")),
    Cuec("B-AU-6-01", "bank", "AU-6", "Review security events and the action log",
         "Send OpenClear's security events to the bank's SIEM (or read them through the API), alert on the ones that matter to "
         "the bank, and review the action log for unusual activity.",
         "OpenClear records every state-changing action in a signed, hash-chained log, and delivers security events in order and "
         "at least once.",
         "automated", "event_delivery", "Continuous",
         "Settings → Security event forwarding; Controls → Security events delivered", related=("AU-6(1)", "SI-4")),
    Cuec("B-AU-11-01", "bank", "AU-11", "Keep the evidence",
         "Take an evidence export at least quarterly and keep it, with the action log's archives, for as long as the bank's "
         "retention schedule requires, in storage the bank controls; under New York DFS Part 500 (500.6), keep decision records "
         "five years and security events three, in the bank's SIEM, since OpenClear keeps security events 30 days by default.",
         "OpenClear's evidence export is signed, so it can be checked years later without OpenClear.",
         "assisted", "evidence_export", "Quarterly",
         "Settings → Controls → Export evidence", related=("SI-12",)),
    # --- The bank: processing ------------------------------------------------------------
    Cuec("B-SI-4-01", "bank", "SI-4", "Decide every exception in time",
         "Staff the exception queue so every exception is decided, by the customer or the bank, before its deadline.",
         "OpenClear raises exceptions with their deadline, reminds before the cutoff, and applies the default disposition to "
         "anything left undecided.",
         "automated", "sla", "Daily",
         "Controls → Decisions and imports on time", related=("IR-4",)),
    Cuec("B-SI-4-02", "bank", "SI-4", "Monitor incoming ACH credits for fraud",
         "Run, and review at least yearly, the bank's own risk-based process to identify incoming ACH credits initiated by "
         "fraud, as the Nacha Rules require of every RDFI from 2026.",
         "OpenClear's ACH positive pay monitors debits only and isn't that process; its evidence export summarizes debits, "
         "exceptions and returns for the bank's fraud-monitoring file.",
         "attested", None, "Yearly", "Settings → Controls → Export evidence (ach_monitoring)", related=("RA-3",)),
    Cuec("B-IR-6-03", "bank", "IR-6", "Treat an unauthorized consumer return as a notice of error",
         "When a consumer account's ACH debit is returned as unauthorized through OpenClear, treat it as the consumer's notice "
         "under Regulation E and meet the investigation, provisional-credit and reporting times in 12 CFR 1005.11.",
         "OpenClear records the decision, its reason, who made it and when, with notes and images, in the evidence and data "
         "exports.",
         "attested", None, "Yearly", "Exceptions; Settings → Controls → Export evidence", related=("IR-4",)),
    Cuec("B-IR-6-04", "bank", "IR-6", "Return late unauthorized consumer debits on time",
         "For a consumer debit reported unauthorized after OpenClear's decision, return it by the opening of the sixth banking "
         "day after the bank finishes reviewing the signed Written Statement of Unauthorized Debit.",
         "OpenClear decides debits before they post; a late claim is the bank's, through its core.",
         "attested", None, "Yearly", "The bank's core and procedures", related=("IR-4",)),
    Cuec("B-IR-6-05", "bank", "IR-6", "Name incident contacts",
         "Name at least one incident contact in OpenClear, with the CEO and CIO as the fallback, keep them current, and confirm "
         "them each year.",
         "OpenClear sends the service provider's incident notices to them, and warns while there are none or they're a year old.",
         "automated", "incident_contacts", "Yearly", "Settings → Incident contacts", related=("IR-4",)),
    Cuec("B-IR-6-06", "bank", "IR-6", "Notify the FDIC within 36 hours",
         "Decide whether an incident involving OpenClear is a notification incident and, if so, notify the FDIC supervisory office "
         "as soon as possible and within 36 hours of that determination (12 CFR 304.23).",
         "OpenClear's incident notice gives the bank the facts and when it was told, recorded with its acknowledgement.",
         "attested", None, "Per event", "Settings → Incident contacts (notices received)", related=("IR-4",)),
    Cuec("B-IR-6-07", "bank", "IR-6", "Notify New York DFS within 72 hours",
         "A bank supervised by New York DFS notifies DFS within 72 hours of determining that a cybersecurity incident occurred at "
         "OpenClear's host, and updates DFS with material new information (23 NYCRR 500.17(a)).",
         "OpenClear's incident notice gives the bank the facts and when it was told.",
         "attested", None, "Per event", "Settings → Incident contacts (notices received)", related=("IR-4",)),
    Cuec("B-IR-6-08", "bank", "IR-6", "Report check fraud to FinCEN, and keep SARs out of OpenClear",
         "Decide and file suspicious activity reports for check fraud by the bank's own procedure, using OpenClear's records as "
         "supporting documentation (with the key term FIN-2023-MAILTHEFT where it applies), and never record in OpenClear that a "
         "SAR was or wasn't filed (31 CFR 1020.320(e)).",
         "OpenClear keeps the victim-side facts a narrative needs (as issued, as presented, images, decisions, branch attempts) and "
         "has no field for a SAR; customer users and support staff can read notes and reasons.",
         "attested", None, "Per event", "docs/compliance/fincen-ofac.md", related=("IR-4",)),
    Cuec("B-IR-6-02", "bank", "IR-6", "Obtain and keep Written Statements of Unauthorized Debit",
         "Obtain a Written Statement of Unauthorized Debit from a customer who reports a consumer ACH debit as unauthorized, "
         "as the Nacha Rules require, and keep it for the period they set.",
         "OpenClear shows customers the bank's own statement form (Markdown, HTML or a PDF) and warns while a bank that "
         "returns consumer debits has none published.",
         "assisted", "wsud_form", "Yearly", "Settings → Unauthorized debit statement", related=("AU-11",)),
    Cuec("B-SI-3-01", "bank", "SI-3", "Scan files for malware",
         "Use commercially standard malware detection on the computers and systems that send files to OpenClear, and on "
         "files staff download from it.",
         "OpenClear accepts only the file types it reads, re-encodes images, and can check every upload with ClamAV and "
         "VirusTotal's hash lookups, refusing malware and quarantining a file found later (both optional).",
         "attested", None, "Yearly", "Settings → Outside services (malware scanning); file.malware_detected in the action log",
         related=("SI-4",)),
    Cuec("B-CA-3-03", "bank", "CA-3", "Put loss allocation in the positive pay agreement",
         "Agree in writing with each positive pay customer the cut-off, the default decision, that undecided items follow it, the "
         "customer's duty to send issue files before checks are presented and to review exceptions, and how loss falls if it "
         "doesn't (UCC 4-103), and have counsel confirm it under the state's UCC.",
         "OpenClear records the settings the agreement names, and for each item when it was raised, shown, decided and by whom.",
         "attested", None, "On change; reviewed yearly", "Customers → Default decision; Settings → Decision cut-off",
         related=("SA-9",)),
    Cuec("B-CA-3-04", "bank", "CA-3", "Return checks in time",
         "Set cut-offs so the core can send every return before the midnight deadline and Regulation CC's expeditious-return "
         "time (12 CFR 229.31(b)), and give notice of nonpayment for returns of $5,000 or more (229.31(c)) from the core.",
         "OpenClear keeps each check's decision deadline within its midnight deadline, counted from the day it was presented, "
         "and flags a decision sent after it.",
         "automated", "return_deadline", "Daily", "Settings → Decision cut-off; Controls → Checks decided within their return deadline",
         related=("SI-4",)),
    Cuec("B-SI-4-03", "bank", "SI-4", "Tell OpenClear the bank's banking days",
         "Record the days the bank is closed, and whether it opens on the Federal Reserve's holidays, so deadlines follow the "
         "bank's banking days.",
         "OpenClear counts a check's return deadline in the bank's banking days.",
         "attested", None, "Yearly", "Settings → Decision cut-off", related=("CP-2",)),
    Cuec("B-AU-11-02", "bank", "AU-11", "Keep decision records for disputes",
         "Keep exception, decision and image records at least as long as UCC claims can be brought (at least a year, UCC "
         "4-406(f)), plus any litigation hold.",
         "OpenClear keeps every decision, its revisions and the action log, signed, and exports them.",
         "attested", None, "Yearly", "Settings → Controls → Export evidence; Settings → Data export", related=("SI-12",)),
    Cuec("B-CA-3-01", "bank", "CA-3", "Connect the core",
         "Agree and document the interface with the core system (presented items in, decisions out), and confirm decisions are "
         "applied in the core before its cutoff.",
         "OpenClear delivers decisions through the decision outbox, retries, and reports a late or failing delivery.",
         "automated", "decision_delivery", "Daily",
         "Settings → Decision delivery; Controls → Decisions and imports on time", related=("SI-7",)),
    Cuec("B-SI-7-01", "bank", "SI-7", "Reconcile presented items",
         "Reconcile each presented-items file OpenClear loads (its item count and total) against what the core sent, and "
         "investigate a difference. A posted-check layout that names the file's trailer does this for you; reconcile the "
         "files without one yourself.",
         "OpenClear stores each file with its SHA-256 and a signature, and reports the items and total it loaded. A file read "
         "by a posted-check layout with a trailer is refused whole, and raised as a security event, when its lines don't "
         "add up to the trailer.",
         "assisted", "file_reconciliation", "Daily",
         "Bulk uploads; the file's details", related=("SI-10", "CA-3")),
    Cuec("B-CA-3-02", "bank", "CA-3", "Agree reverse positive pay in writing",
         "Before putting a customer's account on reverse positive pay, agree it in writing with the customer, including that "
         "every check they don't return by the cut-off is paid, and keep the agreement.",
         "OpenClear puts an account on reverse positive pay only as a protected change, and lists the accounts on it and those "
         "put on it in the last 30 days.",
         "assisted", "reverse_accounts_agreed", "On change", "Customers → an account's Check review", related=("SA-9",)),
    Cuec("B-CP-2-02", "bank", "CP-2", "Set the teller fallback",
         "Decide what the teller platform does when OpenClear doesn't answer a verification in time (for example, decline "
         "above an amount), configure it in the teller platform, and train tellers on it.",
         "OpenClear answers a verification quickly and says plainly when a check can't be cashed, but can't enforce what a "
         "teller does when it can't be reached.",
         "attested", None, "Yearly", "The teller platform; docs/guides/teller.md", related=("SI-4",)),
    Cuec("B-CP-2-01", "bank", "CP-2", "Plan for an outage at the cutoff",
         "Include positive pay in the bank's business continuity plan: what happens to exceptions and decisions if OpenClear or "
         "the core is unavailable at the cutoff, and who decides by hand.",
         "OpenClear applies each account's default disposition to undecided exceptions, and keeps the decisions to send once the "
         "core is back.",
         "assisted", "default_dispositions", "Yearly", "Customers → Positive pay settings", related=("CP-4",)),
    Cuec("B-CP-2-03", "bank", "CP-2", "Exercise the cutoff outage plan",
         "Exercise B-CP-2-01's plan at least yearly, including getting pay and return decisions to the core before the return "
         "deadline without OpenClear, check the bank's recovery objectives against the host's, and record the result.",
         "OpenClear shows the host's recovery objectives and last restore test on the Controls page.",
         "assisted", "recovery_objectives", "Yearly", "Controls → Recovery objectives stated", related=("CP-4",)),
    Cuec("B-AT-3-01", "bank", "AT-3", "Train the deciders",
         "Train staff who decide exceptions (fraud red flags, return deadlines and reasons, when to call the customer) before they "
         "get access, and each year.",
         "OpenClear shows the information a decision needs, but can't judge whether the person deciding has been trained.",
         "attested", None, "Yearly", "The bank's training records"),
    Cuec("B-IR-6-01", "bank", "IR-6", "Handle incidents involving OpenClear",
         "Include OpenClear in the bank's incident response plan, keep the bank's incident contacts current with whoever hosts it, "
         "and report incidents involving it as the bank's rules require, notifying customers and the FDIC as 12 CFR 364 App. B "
         "Supplement A requires even when the unauthorized access was at the host.",
         "OpenClear's action log, security events and evidence export reconstruct who did what, and when.",
         "attested", None, "Yearly", "The bank's incident response plan", related=("IR-4", "IR-8")),
    Cuec("B-SA-4-01", "bank", "SA-4", "Check accessibility against the bank's obligations",
         "Compare OpenClear's accessibility statement with the bank's own ADA and policy obligations, and report any barrier the "
         "bank's staff or customers meet.",
         "OpenClear checks WCAG 2.2 A and AA automatically on every build, and states what isn't yet checked.",
         "attested", None, "Yearly", "docs/compliance/accessibility.md"),
    # --- The bank: outside services ------------------------------------------------------
    Cuec("B-SA-9-02", "bank", "SA-9", "Write OpenClear into the third-party policy",
         "Assess OpenClear and its host under the bank's third-party service provider policy before use and periodically, with a "
         "contract covering access controls and multi-factor sign-in, encryption, notice of cybersecurity events affecting the "
         "bank or unauthorized access to its customers' information, representations and warranties, and deletion at exit "
         "(23 NYCRR 500.11; 12 CFR 364 App. B III.D; 201 CMR 17.03(2)(f)).",
         "OpenClear supplies the controls, CUECs, host attestations, security metrics and evidence export a contract and an "
         "assessment rely on.",
         "attested", None, "Yearly", "docs/compliance/tprm.md; docs/compliance/nydfs.md"),
    Cuec("B-SA-9-03", "bank", "SA-9", "Tell the FDIC about the service relationship",
         "Notify the FDIC of the relationship with OpenClear's host within 30 days of the contract or the first service, whichever "
         "is first (Bank Service Company Act § 7(c)).",
         "OpenClear's documentation names the services it performs, for the notice to describe.",
         "attested", None, "Once, and on change", "The bank's records"),
    Cuec("B-SA-11-01", "bank", "SA-11", "Evaluate OpenClear as an externally developed application",
         "Review OpenClear's security evidence (the ASVS crosswalk, the threat model, advisories and each release's security "
         "metrics) each year and before a major upgrade (23 NYCRR 500.8).",
         "OpenClear publishes them with each release.",
         "attested", None, "Yearly", "docs/compliance/asvs.md; docs/security/threat-model.md; the release notes"),
    Cuec("B-SA-9-01", "bank", "SA-9", "Assess the outside services turned on",
         "Assess each outside service the bank turns on (email and SMS providers, cloud OCR, object storage, identity providers, "
         "a hosting provider) as a third party, and review it each year.",
         "OpenClear turns no outside service on by itself, and shows which ones are on.",
         "assisted", "outside_services", "Yearly", "Settings; docs/compliance/tprm.md", related=("SR-6",)),
    Cuec("B-SC-8-01", "bank", "SC-8", "Secure the bank's integrations",
         "Point webhooks and decision delivery at HTTPS endpoints on the bank's systems, and check each webhook's signature "
         "before acting on it.",
         "OpenClear signs every webhook, and delivers only to a public HTTPS address.",
         "assisted", "integration_tls", "On change", "Settings → API clients and webhooks", related=("SC-8(1)", "CA-3")),

    # --- The business customer -----------------------------------------------------------
    Cuec("C-AC-2-01", "customer", "AC-2", "Manage the customer's own people",
         "When the customer manages its own people, add only people it has approved, change their role when their job changes, "
         "and deactivate them the day they leave.",
         "OpenClear keeps a customer's people to its own accounts and to the roles the bank offers, and records every change.",
         "assisted", "customer_dormant_users", "Per event; reviewed quarterly", "People; the customer's action log",
         related=("PS-4", "AC-2(3)")),
    Cuec("C-AC-2-02", "customer", "AC-2", "Tell the bank about joiners and leavers",
         "When the bank manages the customer's people, tell the bank promptly when someone joins, changes role or leaves.",
         "OpenClear removes a person's access as soon as the bank deactivates them.",
         "attested", None, "Per event", "The customer's own records", related=("PS-4",)),
    Cuec("C-AC-2(1)-01", "customer", "AC-2(1)", "Run the customer's identity provider",
         "When the customer's people sign on through its own identity provider, require a second factor there and disable leavers there.",
         "OpenClear accepts only the customer's verified email domains from its identity provider, and applies SCIM changes as they arrive.",
         "assisted", "customer_sso", "Continuous", "Sign-on (customer)", related=("IA-2(2)", "PS-4")),
    Cuec("C-AC-6(7)-01", "customer", "AC-6(7)", "Review the customer's access",
         "At least quarterly, review who in the customer can see and decide its items, and remove what's no longer needed.",
         "OpenClear lists the customer's people with their role, accounts and last sign-in.",
         "assisted", "customer_access_review", "Quarterly", "People → Export", related=("AC-2",)),
    Cuec("C-AC-5-01", "customer", "AC-5", "Separate issuing from deciding",
         "Where the customer's size allows, keep apart the people who issue checks or upload issue files and the people who "
         "decide exceptions.",
         "OpenClear never lets the person who submitted an item decide its exception.",
         "automated", "customer_sod", "Quarterly", "People", related=("AC-6",)),
    Cuec("C-IA-2-01", "customer", "IA-2", "Protect sign-in",
         "Sign in with a security key or passkey, or through the customer's identity provider, and never share credentials.",
         "OpenClear supports FIDO2 security keys and passkeys, and the customer's own identity provider.",
         "automated", "customer_mfa", "Continuous", "People; Security", related=("IA-2(2)", "IA-5")),
    Cuec("C-IA-5-01", "customer", "IA-5", "Look after the customer's API credentials",
         "Rotate the customer's API client secrets (or use key pairs), keep them out of source code and shared drives, and revoke "
         "a client when its system is retired.",
         "OpenClear shows each secret only once, stores only its hash, and supports key pairs.",
         "automated", "customer_api_client_secrets", "Continuous", "API clients (customer)", related=("IA-9",)),
    Cuec("C-SI-7-01", "customer", "SI-7", "Send every issued check",
         "Send OpenClear every check the customer issues, and every void, before the check can be presented, complete and "
         "accurate (serial, amount, date, and payee where the bank matches payees).",
         "OpenClear matches presented items against what the customer sent, and raises an exception for anything that differs.",
         "assisted", None, "Every issue run", "Issued items; uploads", related=("SI-10",)),
    Cuec("C-SI-10-01", "customer", "SI-10", "Correct rejected rows",
         "Review each upload's result and correct or resubmit any rejected rows the same business day.",
         "OpenClear validates every row, rejects what it can't match on, and says why.",
         "assisted", "rejected_rows", "Every upload", "Uploads → result", related=("SI-7",)),
    Cuec("C-SI-4-02", "customer", "SI-4", "Review statements promptly",
         "Examine bank statements and paid items with reasonable promptness, and report any unauthorized or altered item to "
         "the bank at once (UCC 4-406).",
         "OpenClear shows each exception with its deadline, and records what the customer saw and decided.",
         "attested", None, "Monthly", "The bank's statements; Exceptions", related=("IR-6",)),
    Cuec("C-SI-4-01", "customer", "SI-4", "Decide exceptions every day",
         "Review exceptions every business day, decide each before its deadline, and return anything not recognized.",
         "OpenClear raises exceptions with their deadline, reminds before the cutoff, and applies the default disposition otherwise.",
         "automated", "customer_decision_timeliness", "Daily", "Exceptions", related=("IR-4",)),
    Cuec("C-AC-3-01", "customer", "AC-3", "Keep ACH authorizations current",
         "Keep the ACH debit authorizations (who may debit each account, and up to how much) current, and revoke one when the "
         "relationship ends.",
         "OpenClear raises an exception for an ACH debit no authorization covers.",
         "assisted", "stale_ach_authorizations", "Quarterly", "ACH authorizations"),
    Cuec("C-IR-6-01", "customer", "IR-6", "Report fraud at once",
         "Report suspected fraud or an unauthorized debit to the bank at once, and complete the bank's statement of "
         "unauthorized debit when the bank asks for one (the Nacha Rules require it for consumer accounts).",
         "OpenClear shows the customer the bank's statement form, and returns the debit when the customer decides to.",
         "attested", None, "Yearly", "Unauthorized debit statement; Exceptions", related=("IR-4",)),
    Cuec("C-SC-28-01", "customer", "SC-28", "Protect what's downloaded",
         "Treat reports, exports and check images downloaded from OpenClear as confidential: store them encrypted, share them only "
         "with those who need them, and delete them when no longer needed.",
         "OpenClear limits downloads to the customer's own accounts and records each export.",
         "attested", None, "Yearly", "The customer's own records", related=("MP-4", "SI-12")),
    Cuec("C-AT-2(3)-01", "customer", "AT-2(3)", "Train against fraud and social engineering",
         "Train the people who issue checks and decide exceptions on check fraud and social engineering, such as fake requests to "
         "change payment details or to pay an item.",
         "OpenClear shows the information a decision needs, but can't judge whether the person deciding has been trained.",
         "attested", None, "Yearly", "The customer's training records", related=("AT-2",)),
    Cuec("C-CM-4-01", "customer", "CM-4", "Test integration changes",
         "Test changes to the customer's own integration (issue-file exports, API clients) in the sandbox before using them in production.",
         "OpenClear's sandbox runs the same API and file formats as production.",
         "attested", None, "Per change", "The customer's change records"),

    # --- Whoever runs OpenClear ----------------------------------------------------------
    Cuec("H-SC-28(1)-01", "host", "SC-28(1)", "Encrypt storage at rest",
         "Encrypt the database, file storage (check images, uploads, archives) and backups at rest, and record how in "
         "OPENCLEAR_STORAGE_ENCRYPTION_ATTESTATION.",
         "OpenClear encrypts stored secrets itself (AES-256-GCM) and shows the host's statement on the Controls page.",
         "assisted", "storage_encryption", "On change; reviewed yearly",
         "Controls → Disk and database encryption", related=("SC-28", "SC-12")),
    Cuec("H-SC-12-01", "host", "SC-12", "Manage the keys",
         "Generate the signing keys and the field-encryption key outside the image, keep them in a secret store, restrict who can "
         "read them, and rotate them on the host's schedule.",
         "OpenClear refuses to start in production with development keys, versions its encryption key and re-encrypts on rotation.",
         "assisted", "key_age", "Yearly", "Controls → Production settings", related=("SC-13",)),
    Cuec("H-SC-8(1)-01", "host", "SC-8(1)", "Serve only over HTTPS",
         "Terminate HTTPS with a current certificate and modern TLS in front of OpenClear, and never serve it over plain HTTP.",
         "OpenClear sets secure, SameSite cookies and a strict Content Security Policy.",
         "automated", "transport_security", "Continuous", "scripts/edge_check.py", related=("SC-8", "SC-23")),
    Cuec("H-SC-7-01", "host", "SC-7", "Lock down the origin",
         "Let only the edge network reach OpenClear, keep the database and storage off the internet, and record how the origin "
         "is locked down.",
         "OpenClear checks edge headers come from trusted ranges, and accepts an edge secret.",
         "automated", "edge", "Continuous", "Controls → Edge network set up", related=("SC-5",)),
    Cuec("H-CM-6-01", "host", "CM-6", "Run with production settings",
         "Run production with production settings: real keys, no development defaults, and the scheduled control tests on.",
         "OpenClear checks its own settings at startup and on the Controls page.",
         "automated", "production_safety", "Continuous", "Controls → Production settings", related=("CM-2",)),
    Cuec("H-CP-9-01", "host", "CP-9", "Back up and test restores",
         "Back up the database and file storage, encrypted and kept apart from the primary, with at least one copy immutable or "
         "offline behind sign-in separate from production; state how long backups are kept; and test a clean restore at least "
         "yearly.",
         "OpenClear's database migrations upgrade and downgrade, its action log verifies after a restore, and "
         "scripts/restore_drill.py does both against a scratch database and signs the result; the Controls page reads the "
         "host's backup manifest (Backups fresh) and the last passed drill (Recovery objectives stated).",
         "attested", None, "Daily; restore tested yearly", "The host's backup records; Controls → Backups fresh; the restore drill",
         related=("CP-4", "CP-10")),
    Cuec("H-CP-2-01", "host", "CP-2", "State recovery objectives",
         "State the installation's recovery time and recovery point objectives, record them and each restore test's date in "
         "OpenClear's settings, and give the banks the restore tests' results.",
         "OpenClear shows them on every bank's Controls page, and warns when they're missing or the test is over a year old.",
         "automated", "recovery_objectives", "Yearly", "Controls → Recovery objectives stated", related=("CP-4",)),
    Cuec("H-SI-2-01", "host", "SI-2", "Apply updates",
         "Apply OpenClear releases, and operating system, database and dependency patches, within the host's patching deadlines.",
         "OpenClear publishes versioned releases with a changelog, and shows the running version.",
         "assisted", "software_currency", "Monthly", "/openapi.json version; CHANGELOG.md", related=("RA-5",)),
    Cuec("H-RA-5-01", "host", "RA-5", "Scan for vulnerabilities",
         "Scan the servers, container images and database for vulnerabilities, and fix what's found within the host's deadlines.",
         "OpenClear's source and dependencies are public for anyone to scan.",
         "attested", None, "Monthly", "The host's scan records", related=("SI-2",)),
    Cuec("H-AC-6(5)-01", "host", "AC-6(5)", "Limit administrative access to the host",
         "Limit access to the servers, database and secret store to named administrators using a second factor, and log it. "
         "Platform staff reach a bank's data only through the bank's support access grants.",
         "OpenClear gives platform staff no way into a bank except a grant the bank makes.",
         "assisted", "platform_staff", "Quarterly", "Support access; the host's access records", related=("IA-2(1)", "AC-2")),
    Cuec("H-AU-9(2)-01", "host", "AU-9(2)", "Keep logs off the host",
         "Send the installation's security events and logs to a system the application's administrators can't change, and keep "
         "action-log archives in write-once storage.",
         "OpenClear signs and chains the action log, and delivers security events to the installation's destination.",
         "automated", "event_delivery", "Continuous", "Controls → Security events delivered", related=("AU-11", "AU-6")),
    Cuec("H-SI-4-01", "host", "SI-4", "Watch that it's running",
         "Monitor /health, the job queue and the scheduled jobs, and alert someone when they stop.",
         "OpenClear reports its health and each scheduled job's last run.",
         "assisted", "scheduled_jobs", "Continuous", "/health; Controls", related=("CA-7",)),
    Cuec("H-IR-6-01", "host", "IR-6", "Notify banks of incidents",
         "Issue an incident notice in OpenClear (Platform → Incidents) to each affected bank as soon as possible after determining "
         "an incident that has disrupted or degraded its service, or is likely to, for four hours or more (12 CFR 304.24), or a "
         "cybersecurity event affecting its systems or data (23 NYCRR 500.11(b)(3)), and follow the runbook's Incidents section.",
         "OpenClear emails each bank's incident contacts, shows the notice until the bank acknowledges it, and records both.",
         "attested", None, "Per event", "Platform → Incidents; the host's incident records", related=("IR-4", "IR-8")),
    Cuec("H-IR-6-02", "host", "IR-6", "Notify banks of unauthorized access to their customers' information",
         "Notify each affected bank as soon as possible of unauthorized access to its customer information, whether or not it "
         "disrupted the service, with what the logs show was reached (12 CFR 364 App. B Supplement A).",
         "OpenClear's action log records who viewed check images and who exported data, so the reach can be shown.",
         "attested", None, "Per event", "Platform → Incidents; the bank's action log", related=("IR-4",)),
    Cuec("H-PS-3-01", "host", "PS-3", "Screen the host's staff",
         "Screen everyone with access to the installation or to banks' data before granting it, as the banks' contracts require "
         "(12 CFR 364 App. B III.C.1.e).",
         "OpenClear gives platform staff no way into a bank except a grant the bank makes, and logs what they do.",
         "attested", None, "On hire", "The host's personnel records"),
    Cuec("H-IR-8-01", "host", "IR-8", "Keep an incident response plan",
         "Keep an incident response and recovery plan for the installation (roles, decisions, communications with the banks, "
         "root cause), and test it each year.",
         "OpenClear's runbook has an Incidents section to build the plan on.",
         "attested", None, "Yearly", "RUNBOOK.md (Incidents)", related=("IR-4",)),
    Cuec("H-AT-2-01", "host", "AT-2", "Train the host's staff",
         "Give everyone with access to the installation security awareness training, including social engineering, at least "
         "yearly.",
         "OpenClear limits platform staff to what their role allows and logs what they do.",
         "attested", None, "Yearly", "The host's training records", related=("AT-2(3)",)),
    Cuec("H-CA-8-01", "host", "CA-8", "Test penetration yearly",
         "Have a qualified party penetration-test the installation at least yearly, and share the summary with the banks.",
         "OpenClear's security metrics, advisories and ASVS crosswalk are published with each release.",
         "attested", None, "Yearly", "The test report", related=("RA-5",)),
    Cuec("H-MP-6-01", "host", "MP-6", "Delete a bank's data when it leaves",
         "When a bank leaves, offboard it in OpenClear (Platform → the bank's Offboarding) as the contract says, send the bank the "
         "signed deletion certificate, and tell it how long the host's backups keep copies.",
         "OpenClear tells the bank to take its final export, deletes every row and file of the bank after a wait and two "
         "operators' approval, and signs a certificate of what was deleted.",
         "attested", None, "Per event", "Platform → the bank's Offboarding; the deletion certificate", related=("SI-12",)),
    Cuec("H-CM-4-01", "host", "CM-4", "Announce maintenance",
         "Announce scheduled maintenance and upgrades to each bank beforehand, as a maintenance notice in OpenClear, so they aren't "
         "incidents under 12 CFR 304.24(b).",
         "OpenClear sends maintenance notices the same way as incident notices.",
         "attested", None, "Per event", "Platform → Incidents", related=("CM-3",)),
)


def by_id() -> dict[str, Cuec]:
    return {c.id: c for c in CUECS}


def for_party(party: str) -> list[Cuec]:
    return [c for c in CUECS if c.party == party and not c.withdrawn]
