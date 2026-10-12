# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

# Importing this package must register every ORM mapper, even though most call sites
# only need one or two models directly. SQLAlchemy resolves ForeignKey("table.id")
# strings lazily against whatever's been mapped so far — if a model referenced only by
# FK string (e.g. Account, via issued_item.account_id) was never actually imported
# anywhere in the running process, mapper configuration fails at first flush with
# NoReferencedTableError, even though every individual module imports cleanly on its own.
from openclear.domain import (  # noqa: F401
    account,
    api_client,
    ach_authorization_rule,
    ach_return_reason,
    ach_transaction,
    audit_log_entry,
    billing,
    change_request,
    check_return_reason,
    bulk_upload_created_record,
    bulk_upload_file,
    check_image,
    conformance,
    control_run,
    customer,
    customer_disposition_setting,
    customer_ml_setting,
    data_export_job,
    decision,
    decision_outbox,
    deadline_reminder,
    embed_launch,
    exception_item,
    idempotency_record,
    issued_item,
    job,
    ml_model,
    notification,
    paid_item,
    payment_network,
    platform_api_key,
    revoked_session,
    security_event,
    security_event_destination,
    security_group,
    sso_connection,
    stop_payment,
    tenant,
    tenant_extension,
    tenant_membership,
    user,
    webhook,
    webauthn_challenge,
    webauthn_credential,
    wizard_step_ack,
    wsud_statement,
    wsud_statement_transaction,
)
