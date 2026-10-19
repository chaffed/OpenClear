# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""Seeds the bank that ZAP scans (scripts/zap_scan.py): "ZAP Bank" with an administrator, a
customer, one of its accounts and a customer user. Run inside the app's container, against its
database, with ZAP_PASSWORD set:

    docker exec -i -e ZAP_PASSWORD=... openclear-zap python - < scripts/zap_seed.py

Does nothing if the bank already exists. Only for a disposable scan target."""

import os

from openclear.db.session import get_session_factory
from openclear.domain.tenant import Tenant
from openclear.services import account_service, customer_service, provisioning_service, security_group_service, user_service

SLUG = "zap-bank"
ADMIN = "admin@zap-bank.example.com"
CUSTOMER_USER = "pat@zap-customer.example.com"


def main() -> None:
    password = os.environ["ZAP_PASSWORD"]
    session = get_session_factory()()
    try:
        if session.query(Tenant).filter(Tenant.slug == SLUG).first() is not None:
            print("ZAP Bank already there")
            return
        provisioned = provisioning_service.create_tenant_with_admin(
            session, tenant_name="ZAP Bank", tenant_slug=SLUG, admin_email=ADMIN, admin_password=password)
        tenant_id = provisioned.tenant.id
        customer = customer_service.create_customer(session, tenant_id, customer_service.CustomerInput(
            customer_number="ZAP-1", name="ZAP Customer"))
        account_service.create_account(session, tenant_id, account_service.AccountInput(
            account_number="9000000001", name="ZAP Operating", customer_id=customer.id))
        approver = security_group_service.get_security_group_by_name(session, tenant_id, "Approver")
        user_service.create_user_with_membership(session, tenant_id, email=CUSTOMER_USER, password=password,
                                                 security_group_id=approver.id, customer_id=customer.id)
        session.commit()
        print("Seeded ZAP Bank")
    finally:
        session.close()


if __name__ == "__main__":
    main()
