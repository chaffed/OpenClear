# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The OpenClear control inventory (CONTROLS_PLAN.md CT1): what OpenClear itself does to meet a
control objective, each filed under the NIST SP 800-53 Rev. 5 controls it carries out, with
the checks that test it (services/controls_service.py today; the rest arrive with CT2 and CT3)
and the CUECs it relies on (compliance/cuecs.py).

An id is `OC-<family>-<nn>` (OC-AC-05). The family groups controls by theme; the NIST controls
say exactly what each one carries out. Ids are stable: a control that's withdrawn is marked so
and its id is never reused.

- **type**: `preventive` (stops something happening), `detective` (shows that it happened) or
  `corrective` (puts it right);
- **nature**: `built_in` (always on) or `configurable` (depends on a bank's setting; its CUECs
  include the one that sets it).

`scripts/export_cuecs.py` publishes docs/compliance/controls.md from this module, and
`GET /api/v1/compliance/controls` serves it with each check's live status."""

from dataclasses import dataclass, field

from openclear.compliance.crosswalks import csf_for_all, csf_title
from openclear.compliance.cuecs import NIST_TITLES, PLANNED_CHECKS, oscal_id

VERSION = "1.0"

FAMILIES = {
    "AC": "Access control",
    "IA": "Identification and authentication",
    "AU": "Audit and accountability",
    "SC": "System and communications protection",
    "SI": "System and information integrity",
    "CM": "Configuration management",
    "CA": "Assessment and monitoring",
}
TYPES = {
    "preventive": "stops something happening",
    "detective": "shows that it happened",
    "corrective": "puts it right",
}
NATURES = {
    "built_in": "always on",
    "configurable": "depends on a bank's setting",
}

# The checks on the Controls page (compliance/checks.py).
from openclear.compliance.checks import KEYS as LIVE_CHECKS  # noqa: E402


@dataclass(frozen=True)
class OcControl:
    id: str
    title: str
    statement: str  # what OpenClear does
    nist: tuple[str, ...]  # the 800-53 controls it carries out, the main one first
    type: str
    nature: str
    checks: tuple[str, ...]  # LIVE_CHECKS or cuecs.PLANNED_CHECKS
    cuecs: tuple[str, ...]  # the CUECs it relies on
    where: str
    withdrawn: bool = field(default=False)

    @property
    def family(self) -> str:
        return self.id.split("-")[1]

    def check_status(self, check: str) -> str:
        return "live" if check in LIVE_CHECKS else "planned"

    def as_dict(self) -> dict:
        return {
            "id": self.id, "family": self.family, "title": self.title, "statement": self.statement,
            "nist": [{"id": n, "title": NIST_TITLES[n], "oscal_id": oscal_id(n)} for n in self.nist],
            "type": self.type, "nature": self.nature,
            "checks": [{"key": c, "status": self.check_status(c)} for c in self.checks],
            "csf": [{"id": k, "title": csf_title(k)} for k in csf_for_all(self.nist)],
            "cuecs": list(self.cuecs), "where": self.where, "withdrawn": self.withdrawn,
        }


C = OcControl
CONTROLS: tuple[OcControl, ...] = (
    # --- Access control ------------------------------------------------------------------
    C("OC-AC-01", "Permissions checked on every request",
      "Every page, API call and background job checks the person's or API client's permissions, from their security group, "
      "before it reads or changes anything.",
      ("AC-3", "AC-6"), "preventive", "built_in", ("privileged_accounts",), ("B-AC-6-01",),
      "Settings → Security groups; auth/permissions.py"),
    C("OC-AC-02", "Access ends when a person leaves",
      "Adding, changing and removing a person's access is recorded in the action log, and a deactivated person can't sign in "
      "or use a token from that moment. An identity provider can do the same through SCIM.",
      ("AC-2", "AC-2(3)", "AC-2(1)"), "preventive", "built_in", ("dormant_accounts", "access_review", "scim_sync"),
      ("B-AC-2-01", "B-AC-6(7)-01", "B-AC-2(1)-01"), "Users; the action log"),
    C("OC-AC-03", "Customers see only their own",
      "A business customer's people reach only their own customer's accounts, items and decisions, and never the bank's own "
      "permissions. A customer manages its own people and sign-on only when the bank allows it.",
      ("AC-3", "AC-4", "AC-6"), "preventive", "configurable", ("customer_self_service", "customer_dormant_users", "customer_access_review"),
      ("B-AC-2-02", "C-AC-2-01", "C-AC-6(7)-01"), "A customer's page → Self-service; People"),
    C("OC-AC-04", "Access by account",
      "A customer's person can be limited to some of the customer's accounts; every list, item, decision, image, upload and "
      "export they reach is limited to those accounts.",
      ("AC-3", "AC-6"), "preventive", "configurable", (), ("C-AC-2-01",), "People → Choose accounts; db/account_scope.py"),
    C("OC-AC-05", "Segregation of duties",
      "Conflicting permissions can't be combined in one security group, and whoever submitted an item can't decide its exception. "
      "Each bank chooses to warn or enforce; every breach attempt is logged.",
      ("AC-5",), "preventive", "configurable", ("segregation_of_duties", "customer_sod"), ("B-AC-5-01", "C-AC-5-01"),
      "Settings → Segregation of duties"),
    C("OC-AC-06", "Dual control on decisions",
      "When a bank turns dual control on, one person recommends a decision and a different person approves it.",
      ("AC-3(2)", "AC-5"), "preventive", "configurable", ("dual_control",), ("B-AC-3(2)-01",), "Settings → Dual control; Approvals"),
    C("OC-AC-07", "Two people for rule and model changes",
      "Turning on a matching rule and promoting a fraud model wait for a second admin's approval under enforced segregation of "
      "duties, and both steps are logged.",
      ("CM-3", "CM-5", "AC-3(2)"), "preventive", "configurable", ("two_person_changes",), ("B-CM-3-01",), "Settings → Changes"),
    C("OC-AC-08", "Support access only by grant",
      "Platform staff reach a bank's data only through a grant the bank makes: named, with a reason and an end, read-only "
      "unless a second admin allows more, and never to decide.",
      ("AC-3", "MA-4", "AC-6(5)"), "preventive", "built_in", ("support_access_review", "platform_staff"),
      ("B-MA-4-01", "H-AC-6(5)-01"), "Settings → Support access"),
    C("OC-AC-09", "Banks kept apart",
      "Every query runs inside one bank's context, and on PostgreSQL row-level security refuses another bank's rows even if a "
      "query forgets to filter.",
      ("AC-4", "AC-3"), "preventive", "built_in", (), (), "db/tenancy.py; the RLS policies in migrations/"),
    C("OC-AC-10", "Sessions end",
      "Sessions always end after an idle and an absolute timeout, whose lengths the bank sets, and a person can sign out "
      "their other devices.",
      ("AC-12", "SC-23"), "preventive", "built_in", (), (), "Settings → Session timeouts"),
    C("OC-AC-11", "Model data pooling disclosed",
      "A bank on the shared fraud model acknowledges the data-pooling disclosure, recorded with who and when, and can move to a "
      "model trained on its own decisions alone.",
      ("AC-21",), "preventive", "configurable", ("model_pooling_acknowledged",), ("B-AC-21-01",), "Settings → Fraud model"),
    C("OC-AC-12", "ACH debits only from authorized originators",
      "An ACH debit from an originator not on the account's authorizations becomes an exception for the customer to decide.",
      ("AC-3",), "preventive", "configurable", ("stale_ach_authorizations",), ("C-AC-3-01",), "ACH → Authorizations"),
    # --- Identification and authentication ------------------------------------------------
    C("OC-IA-01", "Password policy and lockout",
      "Passwords meet the bank's policy (length and the kinds of character required), and repeated failed sign-ins lock "
      "the account for a time.",
      ("IA-5(1)", "AC-7"), "preventive", "configurable", ("password_policy",), ("B-IA-5(1)-01",), "Settings → Password policy"),
    C("OC-IA-02", "Security keys and passkeys",
      "People sign in with a FIDO2 security key or passkey as a second factor, which a bank or customer can require.",
      ("IA-2(1)", "IA-2(2)", "IA-2(8)"), "preventive", "configurable", ("admin_mfa", "staff_mfa", "customer_mfa"),
      ("B-IA-2(1)-01", "B-IA-2(2)-01", "C-IA-2-01", "B-IA-8-01"), "My security; Settings → Password policy"),
    C("OC-IA-03", "Single sign-on and SCIM",
      "A bank, or a customer the bank allows, signs its people in through its own identity provider (OIDC or SAML), and can "
      "provision and remove them through SCIM.",
      ("IA-2", "IA-8", "AC-2(1)"), "preventive", "configurable", ("scim_sync", "customer_sso"),
      ("B-AC-2(1)-01", "C-AC-2(1)-01"), "Settings → Sign-on; a customer's Sign-on"),
    C("OC-IA-04", "Integration credentials",
      "API clients authenticate with a secret kept only as a hash, or with a key pair (private_key_jwt, each assertion usable "
      "once), and can be limited to reading or to one customer.",
      ("IA-9", "IA-5"), "preventive", "built_in", ("api_client_secrets", "customer_api_client_secrets"),
      ("B-IA-5-01", "C-IA-5-01"), "Settings → API clients"),
    C("OC-IA-05", "Customers' sign-in set by the bank",
      "The bank decides how each customer's people sign in: password and second factor, or the customer's own identity provider.",
      ("IA-8",), "preventive", "configurable", ("customer_mfa",), ("B-IA-8-01",), "A customer's page → Sign-on"),
    # --- Audit and accountability ---------------------------------------------------------
    C("OC-AU-01", "Signed, chained action log",
      "Every change is recorded with who, when, from where and what, each entry chained to the last and signed (ECDSA), so "
      "an entry can't be altered or removed unnoticed.",
      ("AU-2", "AU-3", "AU-9(3)", "AU-10", "AU-12"), "detective", "built_in", ("audit_chain",), (), "Action log"),
    C("OC-AU-02", "The chain verified",
      "The action log's chain and signatures are verified daily and on request, and the Controls page shows the last result.",
      ("AU-9", "SI-7"), "detective", "built_in", ("audit_chain",), (), "Controls → Action log integrity"),
    C("OC-AU-03", "Signed archives",
      "Old action-log entries are archived to signed files before they leave the database, so they still verify.",
      ("AU-11", "AU-9(3)"), "preventive", "built_in", (), ("H-AU-9(2)-01",), "services/audit_archive_service.py"),
    C("OC-AU-04", "Security events delivered",
      "Sign-ins, failures, lockouts, permission changes and control status changes become security events, delivered with "
      "retries to the bank's and the installation's SIEM.",
      ("AU-12", "AU-6(1)", "SI-4"), "detective", "configurable", ("event_delivery",), ("B-AU-6-01", "H-AU-9(2)-01"),
      "Settings → Security event forwarding"),
    C("OC-AU-05", "Signed evidence export",
      "For any period, a signed bundle of the version, rules and models in force, the chain's verification, breaches, changes, "
      "decisions and control status, which an examiner can check with the public key.",
      ("AU-7", "AU-11"), "detective", "built_in", ("evidence_export",), ("B-AU-11-01",), "Controls → Export evidence"),
    # --- System and communications protection ----------------------------------------------
    C("OC-SC-01", "Secrets encrypted in the database",
      "Secrets kept in the database (SSO keys, delivery credentials, plugin settings) are encrypted with AES-256-GCM under "
      "versioned keys, and re-encrypted when a key is rotated.",
      ("SC-28(1)", "SC-12", "SC-13"), "preventive", "built_in", ("field_encryption", "key_age"), ("H-SC-12-01",), "auth/crypto.py"),
    C("OC-SC-02", "Encryption at rest stated",
      "The host's statement of how disks and the database are encrypted is shown on the Controls page, which warns until "
      "there is one.",
      ("SC-28",), "detective", "built_in", ("storage_encryption",), ("H-SC-28(1)-01",), "Controls → Storage encryption"),
    C("OC-SC-03", "Files signed",
      "Every uploaded file is kept with its SHA-256 and a signature, and every webhook and evidence bundle OpenClear sends "
      "is signed.",
      ("SI-7(6)", "SC-8"), "preventive", "built_in", (), (), "An upload's page; webhooks"),
    C("OC-SC-04", "Browser protections",
      "Cookies are HTTP-only, and secure over HTTPS, which is marked with HSTS; every form carries a CSRF token; and a "
      "content security policy limits scripts to OpenClear's own.",
      ("SC-23", "SC-8", "SC-18"), "preventive", "built_in", ("transport_security",), ("H-SC-8(1)-01",), "web/security.py"),
    C("OC-SC-05", "Edge trusted only as configured",
      "The caller's address is taken only from the configured edge's header, an edge secret can be required, and the "
      "Controls page shows the edge in front.",
      ("SC-7",), "preventive", "configurable", ("edge",), ("H-SC-7-01",), "Controls → Edge in front; docs/guides/edge.md"),
    C("OC-SC-06", "Rate limits",
      "Sign-ins, the API and expensive pages are rate limited per person, API client and address.",
      ("SC-5", "AC-7"), "preventive", "built_in", (), (), "web/rate_limit.py"),
    C("OC-SC-07", "Outbound connections checked",
      "Webhooks and event destinations must be public addresses, checked when connecting, and are reported when not HTTPS.",
      ("SC-7", "SC-8"), "preventive", "built_in", ("integration_tls",), ("B-SC-8-01",), "Settings → Webhooks"),
    # --- System and information integrity --------------------------------------------------
    C("OC-SI-01", "Malformed files refused",
      "X9.37, NACHA and delimited files go through hardened parsers that refuse malformed input quickly and cleanly; a "
      "corpus of bad files is run through them on every check.",
      ("SI-10",), "preventive", "built_in", ("parser_hardening",), (), "Controls → Malformed-file handling"),
    C("OC-SI-02", "Uploads checked row by row",
      "Every upload row is validated and its result kept, failed rows can be corrected in the browser, and an upload can be "
      "backed out.",
      ("SI-10", "SI-7"), "corrective", "built_in", ("rejected_rows", "file_reconciliation"), ("C-SI-10-01", "B-SI-7-01"),
      "An upload's page"),
    C("OC-SI-03", "Anomaly capture tested",
      "A daily test injects known fraud patterns (duplicate serials, altered amounts, wrong accounts, malformed fields) into "
      "the bank's live rules in a dry run and records whether each was caught.",
      ("SI-4", "CA-2"), "detective", "configurable", ("anomaly_capture",), ("B-CA-7-02",), "Model risk"),
    C("OC-SI-04", "Replay before a change",
      "A historical file can be replayed through today's rules in a dry run, showing how the result would differ, before a "
      "rule or model change is approved.",
      ("CM-4", "CA-2"), "detective", "built_in", ("change_impact_replay",), ("B-CM-4-01",), "Model risk → Replay"),
    C("OC-SI-05", "Decisions before the deadline",
      "Each exception shows its deadline, reminders go out before the cutoff, and an undecided exception takes the account's "
      "default disposition.",
      ("SI-4", "CP-2"), "corrective", "configurable", ("sla", "default_dispositions", "customer_decision_timeliness"),
      ("B-SI-4-01", "B-CP-2-01", "C-SI-4-01"), "Exceptions; Settings → Reminders"),
    C("OC-SI-06", "Decisions delivered to the core",
      "Decisions go to the core through an outbox with retries, as files or calls, so none is lost if the core is down.",
      ("CA-3",), "corrective", "built_in", ("decision_delivery",), ("B-CA-3-01",), "Settings → Decision files"),
    C("OC-SI-07", "Unauthorized debits signed for",
      "A customer signs a Written Statement of Unauthorized Debit for a returned consumer ACH debit, kept with its consent "
      "and attestation text.",
      ("IR-6", "AU-10"), "detective", "built_in", ("wsud_signed",), ("C-IR-6-01",), "Written statements of unauthorized debit"),
    # --- Configuration management ---------------------------------------------------------
    C("OC-CM-01", "Production settings enforced",
      "In production OpenClear refuses to start with the checked-in keys and secrets, placeholder legal text or incomplete "
      "storage settings, and the Controls page reports the settings in force.",
      ("CM-6",), "preventive", "built_in", ("production_safety",), ("H-CM-6-01",), "Controls → Production safety"),
    C("OC-CM-02", "Versioned releases and migrations",
      "Each release is versioned with a changelog, its database migrations upgrade and downgrade, and the running version is "
      "published.",
      ("CM-3", "SI-2", "CP-10"), "preventive", "built_in", ("software_currency",), ("H-SI-2-01",), "/openapi.json; CHANGELOG.md"),
    C("OC-CM-03", "What's running is recorded",
      "The evidence export records the version, the plugins and models in force with their digests, and the outside services "
      "turned on.",
      ("CM-2", "CM-8"), "detective", "built_in", ("outside_services",), ("B-SA-9-01",), "Controls → Export evidence"),
    # --- Assessment and monitoring --------------------------------------------------------
    C("OC-CA-01", "Controls checked continuously",
      "Each control is checked when the Controls page or API is read and hourly; a change of status becomes a security event.",
      ("CA-7",), "detective", "built_in", ("control_response",), ("B-CA-7-01",), "Controls"),
    C("OC-CA-02", "Health and jobs reported",
      "/health reports whether the database answers, and each scheduled job's last run is kept.",
      ("CA-7", "SI-4"), "detective", "built_in", ("scheduled_jobs",), ("H-SI-4-01",), "/health"),
)
del C


def by_id() -> dict[str, OcControl]:
    return {c.id: c for c in CONTROLS}


def for_check(check: str) -> list[OcControl]:
    return [c for c in CONTROLS if check in c.checks and not c.withdrawn]


def all_checks() -> set[str]:
    return set(LIVE_CHECKS) | set(PLANNED_CHECKS)
