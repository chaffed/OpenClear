# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from fastapi import APIRouter

from openclear.web.routers.accounts import router as accounts_router
from openclear.web.routers.api_clients import router as api_clients_router
from openclear.web.routers.webhooks import router as webhooks_router
from openclear.web.routers.plugins import router as plugins_router
from openclear.web.routers.saml import router as saml_router
from openclear.web.routers.oidc_federation import router as oidc_federation_router
from openclear.web.routers.ach_authorizations import router as ach_authorizations_router
from openclear.web.routers.ach_return_reasons import router as ach_return_reasons_router
from openclear.web.routers.billing import customer_router as billing_customer_router
from openclear.web.routers.billing import router as billing_router
from openclear.web.routers.ach_transactions import router as ach_transactions_router
from openclear.web.routers.admin import router as admin_router
from openclear.web.routers.audit_log import router as audit_log_router
from openclear.web.routers.auth import router as auth_router
from openclear.web.routers.branding import router as branding_router
from openclear.web.routers.bulk_uploads import router as bulk_uploads_router
from openclear.web.routers.check_images import router as check_images_router
from openclear.web.routers.customer_banner import router as customer_banner_router
from openclear.web.routers.markdown_preview import router as markdown_preview_router
from openclear.web.routers.customer_password_policy import router as customer_password_policy_router
from openclear.web.routers.customers import router as customers_router
from openclear.web.routers.dashboard import router as dashboard_router
from openclear.web.routers.data_export import router as data_export_router
from openclear.web.routers.docs import router as docs_router
from openclear.web.routers.exceptions import router as exceptions_router
from openclear.web.routers.fraud_training import router as fraud_training_router
from openclear.web.routers.issued_items import router as issued_items_router
from openclear.web.routers.license import router as license_router
from openclear.web.routers.paid_items import router as paid_items_router
from openclear.web.routers.security_groups import router as security_groups_router
from openclear.web.routers.security_settings import router as security_settings_router
from openclear.web.routers.session import router as session_router
from openclear.web.routers.sso_settings import router as sso_settings_router
from openclear.web.routers.stop_payments import router as stop_payments_router
from openclear.web.routers.tenant_ml import router as tenant_ml_router
from openclear.web.routers.tenant_settings import router as tenant_settings_router
from openclear.web.routers.tenant_switch import router as tenant_switch_router
from openclear.web.routers.theme import router as theme_router
from openclear.web.routers.users import router as users_router
from openclear.web.routers.wizard import router as wizard_router
from openclear.web.routers.wsud import router as wsud_router

web_router = APIRouter()
web_router.include_router(branding_router)
web_router.include_router(auth_router)
web_router.include_router(session_router)
web_router.include_router(dashboard_router)
web_router.include_router(security_settings_router)
web_router.include_router(tenant_switch_router)
web_router.include_router(tenant_ml_router)
web_router.include_router(tenant_settings_router)
web_router.include_router(accounts_router)
web_router.include_router(customers_router)
web_router.include_router(customer_password_policy_router)
web_router.include_router(customer_banner_router)
web_router.include_router(markdown_preview_router)
web_router.include_router(issued_items_router)
web_router.include_router(stop_payments_router)
web_router.include_router(paid_items_router)
web_router.include_router(check_images_router)
web_router.include_router(ach_authorizations_router)
web_router.include_router(ach_transactions_router)
web_router.include_router(ach_return_reasons_router)
web_router.include_router(exceptions_router)
web_router.include_router(fraud_training_router)
web_router.include_router(admin_router)
web_router.include_router(users_router)
web_router.include_router(security_groups_router)
web_router.include_router(bulk_uploads_router)
web_router.include_router(audit_log_router)
web_router.include_router(sso_settings_router)
web_router.include_router(api_clients_router)
web_router.include_router(webhooks_router)
web_router.include_router(plugins_router)
web_router.include_router(saml_router)
web_router.include_router(oidc_federation_router)
web_router.include_router(data_export_router)
web_router.include_router(wizard_router)
web_router.include_router(wsud_router)
web_router.include_router(theme_router)
web_router.include_router(docs_router)
web_router.include_router(license_router)
web_router.include_router(billing_router)
web_router.include_router(billing_customer_router)
