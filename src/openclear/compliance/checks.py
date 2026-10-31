# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Every control check, in one registry (CONTROLS_PLAN.md CT2): its key, title, how it runs and
how often. services/controls_service.py builds the Controls page from it, the hourly runner
(workers/tasks.py::controls_watch_job) and the nightly population runner record each run
(services/control_results_service.py), and compliance/controls.py attaches each to the
controls it tests.

- **kind**: `state` looks at the bank as it is now; `population` looks at every item in a
  period (the previous day, nightly, or any period on demand), so it can test a quarter from
  before it was turned on.
- **schedule**: `hourly` or `nightly`; a page view also works out every check, and records a
  result only when one's status changed.
- **scope**: `bank`, or `installation` for what's the same for every bank on it (the host's
  part). Installation checks are recorded for each bank too, so each bank's history is whole.
- **needs_request**: works from the web request being served (the edge check), so the
  scheduled runner leaves it to page views rather than recording a status it can't see."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

POPULATION_VIEW_WINDOW = timedelta(hours=24)  # what a page view shows for a population check


@dataclass(frozen=True)
class Check:
    key: str
    title: str
    run: Callable  # state: (session, tenant_id) -> Control; population: (session, tenant_id, start, end) -> Control
    kind: str = "state"
    schedule: str = "hourly"
    scope: str = "bank"
    needs_request: bool = False

    def run_now(self, session, tenant_id, *, start: datetime | None = None, end: datetime | None = None):
        if self.kind == "population":
            end = end or datetime.now(timezone.utc)
            return self.run(session, tenant_id, start or end - POPULATION_VIEW_WINDOW, end)
        return self.run(session, tenant_id)


def _registry() -> tuple[Check, ...]:
    from openclear.services import control_checks as c
    from openclear.services import controls_service as s

    return (
        # The thirteen from COMPLIANCE_PLAN.md C4, in the Controls page's order.
        Check("field_encryption", "Secrets encrypted in the database", s._field_encryption),
        Check("storage_encryption", "Disk and database encryption", lambda session, tenant_id: s._storage_encryption(), scope="installation"),
        Check("recovery_objectives", "Recovery objectives stated", lambda session, tenant_id: s._recovery_objectives(session),
              scope="installation"),
        Check("rate_limit_backend", "Rate limits shared across instances", lambda session, tenant_id: s._rate_limit_backend(),
              scope="installation"),
        Check("backup_freshness", "Backups fresh", lambda session, tenant_id: s._backup_freshness(), scope="installation"),
        Check("storage_consistency", "Files and rows agree", lambda session, tenant_id: s._storage_consistency(session),
              scope="installation", schedule="nightly"),
        Check("edge", "Edge network set up", lambda session, tenant_id: s._edge(), scope="installation", needs_request=True),
        Check("audit_chain", "Action log intact", s._audit_chain),
        Check("segregation_of_duties", "Segregation of duties", s._sod),
        Check("admin_mfa", "Admins use a second factor", s._admin_mfa),
        Check("anomaly_capture", "Anomaly capture test", s._anomaly_capture),
        Check("parser_hardening", "Malformed files refused", lambda session, tenant_id: s._parser_hardening(), scope="installation",
              schedule="nightly"),
        Check("sla", "Decisions and imports on time", s._sla),
        Check("api_client_secrets", "Integration secrets rotated", s._api_client_secrets),
        Check("customer_self_service", "Customer self-service", s._customer_self_service),
        Check("event_delivery", "Security events delivered", s._event_delivery),
        Check("production_safety", "Production settings", lambda session, tenant_id: s._production_safety(), scope="installation"),
        # CT2: people.
        Check("dormant_accounts", "Dormant staff accounts", c.dormant_accounts),
        Check("customer_dormant_users", "Dormant customer accounts", c.customer_dormant_users),
        Check("privileged_accounts", "Privileged accounts", c.privileged_accounts),
        Check("staff_mfa", "Staff use a second factor", c.staff_mfa),
        Check("customer_mfa", "Customer people use a second factor", c.customer_mfa),
        Check("customer_sod", "Customers separate issuing from deciding", c.customer_sod),
        # Integrations and sign-on.
        Check("customer_api_client_secrets", "Customers' integration secrets rotated", c.customer_api_client_secrets),
        Check("customer_sso", "Customers' own sign-on", c.customer_sso),
        Check("scim_sync", "Directories in step", c.scim_sync),
        Check("integration_tls", "Integrations use HTTPS", c.integration_tls),
        # Positive pay operations.
        Check("default_dispositions", "Default dispositions set", c.default_dispositions),
        Check("rejected_rows", "Rejected rows corrected", c.rejected_rows),
        Check("stale_ach_authorizations", "ACH authorizations current", c.stale_ach_authorizations, schedule="nightly"),
        Check("wsud_form", "Unauthorized-debit statement form published", c.wsud_form, schedule="nightly"),
        Check("reverse_accounts_agreed", "Reverse positive pay agreed", c.reverse_accounts_agreed, schedule="nightly"),
        Check("return_deadline", "Checks decided within their return deadline", c.return_deadline),
        Check("incident_contacts", "Incident contacts named", c.incident_contacts, schedule="nightly"),
        Check("custom_fields", "Required custom fields filled in", c.custom_fields, schedule="nightly"),
        Check("continuity_gaps", "No outage across a cut-off", c.continuity_gaps),
        Check("evidence_export", "Evidence exported this quarter", c.evidence_export, schedule="nightly"),
        # Population checks, nightly over the previous day.
        Check("dual_control", "Dual control on every decision", c.dual_control, kind="population", schedule="nightly"),
        Check("two_person_changes", "Rule and model changes approved by a second admin", c.two_person_changes, kind="population",
              schedule="nightly"),
        Check("change_impact_replay", "Changes replayed before approval", c.change_impact_replay, kind="population", schedule="nightly"),
        Check("support_access_review", "Support access granted with care", c.support_access_review, kind="population", schedule="nightly"),
        Check("customer_decision_timeliness", "Customers decide in time", c.customer_decision_timeliness, kind="population",
              schedule="nightly"),
        # The installation.
        Check("scheduled_jobs", "Scheduled jobs running", c.scheduled_jobs, scope="installation"),
        Check("transport_security", "HTTPS with HSTS", c.transport_security, scope="installation"),
        Check("platform_staff", "Platform staff use a second factor", c.platform_staff, scope="installation"),
        Check("software_currency", "Software kept up to date", c.software_currency, scope="installation", schedule="nightly"),
        Check("regulatory_currency", "Regulatory pages current", c.regulatory_currency, scope="installation", schedule="nightly"),
        Check("installation_settings", "Installation settings unchanged", c.installation_settings, scope="installation"),
        Check("provider_log", "Service provider's action log intact", c.provider_log, scope="installation"),
        Check("second_approver", "Someone else can approve", c.second_approver),
        Check("sensitive_settings_protected", "Sensitive settings need a second admin", c.sensitive_settings_protected),
        # CT3: access reviews, files, delivery, outside services and the bank's standards.
        Check("access_review", "Access reviewed this quarter", c.access_review, schedule="nightly"),
        Check("customer_access_review", "Customers reviewed their access", c.customer_access_review, schedule="nightly"),
        Check("file_reconciliation", "Presented-items files reconciled", c.file_reconciliation),
        Check("decision_delivery", "Decision files delivered and acknowledged", c.decision_delivery),
        Check("outside_services", "Outside services reviewed", c.outside_services, schedule="nightly"),
        Check("password_policy", "Password policy meets your standard", c.password_policy),
        Check("security_key_required", "A security key is required", c.security_key_required),
        Check("key_age", "Keys rotated on schedule", c.key_age, schedule="nightly"),
        # Deviations themselves (last, so it sees the others' deviations from this round).
        Check("control_response", "Deviations dealt with", c.control_response),
    )


_cache: tuple[Check, ...] | None = None


def registry() -> tuple[Check, ...]:
    global _cache
    if _cache is None:
        _cache = _registry()
    return _cache


def by_key() -> dict[str, Check]:
    return {c.key: c for c in registry()}


# The keys, without importing the services (compliance/controls.py and the CUEC inventory read
# them at import time); tests/test_compliance checks this matches registry().
KEYS = (
    "field_encryption", "storage_encryption", "recovery_objectives", "rate_limit_backend", "backup_freshness", "storage_consistency", "edge", "audit_chain", "segregation_of_duties", "admin_mfa", "anomaly_capture",
    "parser_hardening", "sla", "api_client_secrets", "customer_self_service", "event_delivery", "production_safety",
    "dormant_accounts", "customer_dormant_users", "privileged_accounts", "staff_mfa", "customer_mfa", "customer_sod",
    "customer_api_client_secrets", "customer_sso", "scim_sync", "integration_tls",
    "default_dispositions", "rejected_rows", "stale_ach_authorizations", "wsud_form", "reverse_accounts_agreed", "return_deadline", "incident_contacts", "custom_fields", "continuity_gaps",
    "evidence_export",
    "dual_control", "two_person_changes", "change_impact_replay", "support_access_review", "customer_decision_timeliness",
    "scheduled_jobs", "transport_security", "platform_staff", "software_currency", "regulatory_currency", "installation_settings", "provider_log", "second_approver", "sensitive_settings_protected", "access_review", "customer_access_review", "file_reconciliation",
    "decision_delivery", "outside_services", "password_policy", "security_key_required", "key_age", "control_response",
)
