# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from fastapi import APIRouter

from openclear.api.v1.ach_authorizations import router as ach_authorizations_router
from openclear.api.v1.ach_transactions import router as ach_transactions_router
from openclear.api.v1.admin import router as admin_router
from openclear.api.v1.auth import router as auth_router
from openclear.api.v1.check_images import router as check_images_router
from openclear.api.v1.decisions import router as decisions_router
from openclear.api.v1.dropbox_import import router as dropbox_import_router
from openclear.api.v1.exceptions import router as exceptions_router
from openclear.api.v1.fraud_training import router as fraud_training_router
from openclear.api.v1.issued_items import router as issued_items_router
from openclear.api.v1.paid_items import router as paid_items_router
from openclear.api.v1.platform_usage import router as platform_usage_router
from openclear.api.v1.billing import router as billing_router
from openclear.api.v1.platform_billing import router as platform_billing_router
from openclear.api.v1.platform_jobs import router as platform_jobs_router
from openclear.api.v1.platform_ml import router as platform_ml_router
from openclear.api.v1.stop_payments import router as stop_payments_router
from openclear.api.v1.users import router as users_router
from openclear.api.v1.oauth import router as oauth_router
from openclear.api.v1.customers import router as customers_router
from openclear.api.v1.accounts import router as accounts_router
from openclear.api.v1.decision_feed import router as decision_feed_router
from openclear.api.v1.embed import router as embed_router
from openclear.api.v1.conformance import router as conformance_router
from openclear.api.v1.mrm import router as mrm_router
from openclear.api.v1.compliance import router as compliance_router
from openclear.api.v1.controls import router as controls_router
from openclear.api.v1.audit import router as audit_router
from openclear.api.v1.data_exports import router as data_exports_router
from openclear.api.v1.edge import router as edge_router
from openclear.api.v1.security_events import router as security_events_router
from openclear.api.v1.decision_batches import router as decision_batches_router
from openclear.api.v1.api_clients import router as api_clients_router
from openclear.api.v1.bulk_uploads import router as bulk_uploads_router
from openclear.api.v1.webauthn import router as webauthn_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth_router)
api_router.include_router(webauthn_router)
api_router.include_router(issued_items_router)
api_router.include_router(stop_payments_router)
api_router.include_router(paid_items_router)
api_router.include_router(check_images_router)
api_router.include_router(ach_authorizations_router)
api_router.include_router(ach_transactions_router)
api_router.include_router(exceptions_router)
api_router.include_router(fraud_training_router)
api_router.include_router(decisions_router)
api_router.include_router(admin_router)
api_router.include_router(users_router)
api_router.include_router(oauth_router)
api_router.include_router(customers_router)
api_router.include_router(accounts_router)
api_router.include_router(decision_feed_router)
api_router.include_router(embed_router)
api_router.include_router(conformance_router)
api_router.include_router(mrm_router)
api_router.include_router(controls_router)
api_router.include_router(compliance_router)
api_router.include_router(audit_router)
api_router.include_router(security_events_router)
api_router.include_router(edge_router)
api_router.include_router(data_exports_router)
api_router.include_router(decision_batches_router)
api_router.include_router(api_clients_router)
api_router.include_router(bulk_uploads_router)
api_router.include_router(dropbox_import_router)
api_router.include_router(platform_usage_router)
api_router.include_router(platform_ml_router)
api_router.include_router(platform_jobs_router)
api_router.include_router(billing_router)
api_router.include_router(platform_billing_router)
