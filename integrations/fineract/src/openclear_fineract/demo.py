# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Chaffed

"""Seeds a fresh Fineract as a sandbox core (sandbox/README.md): a "Check" payment type, a
business client, a savings product and an active account with a deposit and two checks
paid from it, so the adapter has something to sync and present."""

from __future__ import annotations

from datetime import date, timedelta

from openclear_fineract.fineract import Fineract

_FMT = {"dateFormat": "yyyy-MM-dd", "locale": "en"}


def seed(fineract: Fineract, *, external_id: str = "CIF-77", checks: tuple[tuple[str, float], ...] = (("1001", 250.0), ("1002", 999.0))) -> dict:
    today = date.today()
    activation = (today - timedelta(days=30)).isoformat()
    payment_types = fineract._request("GET", "/paymenttypes")
    check = next((p for p in payment_types if p.get("name") == "Check"), None)
    check_id = check["id"] if check else fineract._request("POST", "/paymenttypes", json={
        "name": "Check", "description": "Check (cheque) payment", "isCashPayment": False, "position": 10})["resourceId"]
    client_id = fineract._request("POST", "/clients", json={
        **_FMT, "officeId": 1, "legalFormId": 2, "fullname": "Acme Corp", "externalId": external_id, "active": True,
        "activationDate": activation, "submittedOnDate": activation})["clientId"]
    product_id = fineract._request("POST", "/savingsproducts", json={
        "locale": "en", "name": f"Business Checking {external_id}", "shortName": external_id[-4:], "currencyCode": "USD",
        "digitsAfterDecimal": 2, "inMultiplesOf": 0, "nominalAnnualInterestRate": 0, "interestCompoundingPeriodType": 1,
        "interestPostingPeriodType": 4, "interestCalculationType": 1, "interestCalculationDaysInYearType": 365, "accountingRule": 1})["resourceId"]
    account_id = fineract._request("POST", "/savingsaccounts", json={**_FMT, "clientId": client_id, "productId": product_id,
                                                                      "submittedOnDate": activation})["savingsId"]
    fineract._request("POST", f"/savingsaccounts/{account_id}", params={"command": "approve"}, json={**_FMT, "approvedOnDate": activation})
    fineract._request("POST", f"/savingsaccounts/{account_id}", params={"command": "activate"}, json={**_FMT, "activatedOnDate": activation})
    fineract._request("POST", f"/savingsaccounts/{account_id}/transactions", params={"command": "deposit"}, json={
        **_FMT, "transactionDate": activation, "transactionAmount": 10_000, "paymentTypeId": check_id})
    for number, amount in checks:
        fineract._request("POST", f"/savingsaccounts/{account_id}/transactions", params={"command": "withdrawal"}, json={
            **_FMT, "transactionDate": (today - timedelta(days=1)).isoformat(), "transactionAmount": amount,
            "paymentTypeId": check_id, "checkNumber": number})
    account = fineract._request("GET", f"/savingsaccounts/{account_id}")
    return {"client_id": client_id, "account_id": account_id, "account_no": account["accountNo"], "check_payment_type_id": check_id}
