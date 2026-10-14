# Viewport baseline (MOBILE_PLAN.md R0)

Written by `tests/test_web/test_viewports.py` with `OPENCLEAR_VIEWPORT_WRITE_BASELINE=1`.
Each cell: sideways scroll (↔), small touch targets (kinds), Pay/Return below the fold (⬇), axe problems.

| Page | 360px | 768px | 1024px | 1280px |
|---|---|---|---|---|
| dashboard | ✓ | ✓ | ✓ | ✓ |
| exceptions | ✓ | ✓ | ✓ | ✓ |
| exception | ✓ | ✓ | ✓ | ✓ |
| issued_items | ✓ | ✓ | ✓ | ✓ |
| paid_items | ✓ | ✓ | ✓ | ✓ |
| stop_payments | ✓ | ✓ | ✓ | ✓ |
| ach_transactions | ✓ | ✓ | ✓ | ✓ |
| ach_authorizations | ✓ | ✓ | ✓ | ✓ |
| accounts | ✓ | ✓ | ✓ | ✓ |
| customers | ✓ | ✓ | ✓ | ✓ |
| api_clients | ✓ | ✓ | ✓ | ✓ |
| action_log | ✓ | ✓ | ✓ | ✓ |
| users | ✓ | ✓ | ✓ | ✓ |
| sign_on | ✓ | ✓ | ✓ | ✓ |
| control_panel | ✓ | ✓ | ✓ | ✓ |
| upload_form | ✓ | ✓ | ✓ | ✓ |
| upload | 1 small | 1 small | ✓ | ✓ |
| approvals | ✓ | ✓ | ✓ | ✓ |
| customer | 3 small | 3 small | ✓ | ✓ |
| user_edit | 1 small | 1 small | ✓ | ✓ |
| user_new | ✓ | ✓ | ✓ | ✓ |
| users_bulk | ✓ | ✓ | ✓ | ✓ |
| security_groups | 1 small | 1 small | ✓ | ✓ |
| security_group_edit | ✓ | ✓ | ✓ | ✓ |
| check_return_reasons | 7 small | 7 small | ✓ | ✓ |
| ach_return_reasons | ✓ | ✓ | ✓ | ✓ |
| check_images | ✓ | ✓ | ✓ | ✓ |
| webhooks | ✓ | ✓ | ✓ | ✓ |
| webhook_new | 1 small | 1 small | ✓ | ✓ |
| api_client_new | 1 small | 1 small | ✓ | ✓ |
| sso_new | 1 small | 1 small | ✓ | ✓ |
| controls | ✓ | ✓ | ✓ | ✓ |
| changes | ✓ | ✓ | ✓ | ✓ |
| model_risk | ✓ | ✓ | ✓ | ✓ |
| ml_models | ✓ | ✓ | ✓ | ✓ |
| plugins | ✓ | ✓ | ✓ | ✓ |
| integration | ✓ | ✓ | ✓ | ✓ |
| layouts | ✓ | ✓ | ✓ | ✓ |
| billing | ✓ | ✓ | ✓ | ✓ |
| wizard | 7 small | 7 small | ✓ | ✓ |
| search | ✓ | ✓ | ✓ | ✓ |
| my_security | ✓ | ✓ | ✓ | ✓ |
| notifications | 10 small, 10 axe | 10 small, 10 axe | 10 axe | 10 axe |
| setting_branding | 1 small | 1 small | ✓ | ✓ |
| setting_password_policy | ✓ | ✓ | ✓ | ✓ |
| setting_reminders | ✓ | ✓ | ✓ | ✓ |
| setting_security_events | ✓ | ✓ | ✓ | ✓ |
| setting_sod | 2 small | 2 small | ✓ | ✓ |
| setting_support_access | ✓ | ✓ | ✓ | ✓ |
| setting_fraud_model | 1 small | 1 small | ✓ | ✓ |
| settings_dialog | 1 small | 1 small | ✓ | ✓ |
| people | ✓ | ✓ | ✓ | ✓ |
| person | ✓ | ✓ | ✓ | ✓ |
| people_invite | ✓ | ✓ | ✓ | ✓ |
| people_activity | ✓ | ✓ | ✓ | ✓ |
| customer_control_panel | ✓ | ✓ | ✓ | ✓ |

## Details

### exception at 360px
- table scrolls sideways: "What was expected vs. what was presented" (324px in 278px)

### action_log at 768px
- table scrolls sideways: "Action log" (788px in 686px)

### users at 768px
- table scrolls sideways: "Users" (692px in 686px)

### upload_form at 360px
- table scrolls sideways: "Bulk upload issued items" (301px in 278px)

### upload at 360px
- small target: a "Back to issued items" (1x, e.g. 149x18)

### upload at 768px
- small target: a "Back to issued items" (1x, e.g. 149x18)

### customer at 360px
- table scrolls sideways: "Maple Street Dental" (290px in 278px)
- table scrolls sideways: "Accounts" (352px in 278px)
- small target: a "dana@dental.example.com" (1x, e.g. 178x17)
- small target: a "View all accounts" (1x, e.g. 126x18)
- small target: a "Its people and sign-on activity" (1x, e.g. 219x18)

### customer at 768px
- small target: a "dana@dental.example.com" (1x, e.g. 178x17)
- small target: a "View all accounts" (1x, e.g. 126x18)
- small target: a "Its people and sign-on activity" (1x, e.g. 219x18)

### user_edit at 360px
- small target: a "Cancel" (1x, e.g. 50x18)

### user_edit at 768px
- small target: a "Cancel" (1x, e.g. 50x18)

### users_bulk at 360px
- table scrolls sideways: "Bulk upload users" (372px in 278px)

### security_groups at 360px
- small target: a "Edit" (6x, e.g. 25x17)

### security_groups at 768px
- small target: a "Edit" (6x, e.g. 25x17)

### check_return_reasons at 360px
- table scrolls sideways: "Check Return Reasons" (327px in 278px)
- small target: button.link-button "Retire Altered or fictitious item" (1x, e.g. 38x44)
- small target: button.link-button "Retire Duplicate presentment" (1x, e.g. 38x44)
- small target: button.link-button "Retire Not authorized" (1x, e.g. 38x44)
- small target: button.link-button "Retire Refer to maker" (1x, e.g. 38x44)
- small target: button.link-button "Retire Signature irregular" (1x, e.g. 38x44)
- small target: button.link-button "Retire Stale dated" (1x, e.g. 38x44)
- small target: button.link-button "Retire Stop payment" (1x, e.g. 38x44)

### check_return_reasons at 768px
- small target: button.link-button "Retire Altered or fictitious item" (1x, e.g. 38x44)
- small target: button.link-button "Retire Duplicate presentment" (1x, e.g. 38x44)
- small target: button.link-button "Retire Not authorized" (1x, e.g. 38x44)
- small target: button.link-button "Retire Refer to maker" (1x, e.g. 38x44)
- small target: button.link-button "Retire Signature irregular" (1x, e.g. 38x44)
- small target: button.link-button "Retire Stale dated" (1x, e.g. 38x44)
- small target: button.link-button "Retire Stop payment" (1x, e.g. 38x44)

### ach_return_reasons at 360px
- table scrolls sideways: "ACH Return Reasons" (419px in 278px)

### webhook_new at 360px
- small target: a "Back to webhooks" (1x, e.g. 132x18)

### webhook_new at 768px
- small target: a "Back to webhooks" (1x, e.g. 132x18)

### api_client_new at 360px
- small target: a "Back to API clients" (1x, e.g. 134x18)

### api_client_new at 768px
- small target: a "Back to API clients" (1x, e.g. 134x18)

### sso_new at 360px
- small target: a "Cancel" (1x, e.g. 50x18)

### sso_new at 768px
- small target: a "Cancel" (1x, e.g. 50x18)

### controls at 360px
- table scrolls sideways: "Controls" (552px in 278px)

### ml_models at 360px
- table scrolls sideways: "Shared model" (421px in 278px)

### plugins at 360px
- table scrolls sideways: "Plugins" (376px in 278px)

### integration at 360px
- table scrolls sideways: "Integration checklist" (383px in 278px)

### wizard at 360px
- small target: a "Go to Settings" (2x, e.g. 95x17)
- small target: a "Go to Fraud scoring model" (1x, e.g. 176x17)
- small target: a "Go to Security Groups" (1x, e.g. 146x17)
- small target: a "Add an account" (1x, e.g. 103x17)
- small target: a "Add a user" (1x, e.g. 71x17)
- small target: a "Go to Single Sign-On" (1x, e.g. 139x17)
- small target: a "Add a customer" (1x, e.g. 104x17)

### wizard at 768px
- small target: a "Go to Settings" (2x, e.g. 95x17)
- small target: a "Go to Fraud scoring model" (1x, e.g. 176x17)
- small target: a "Go to Security Groups" (1x, e.g. 146x17)
- small target: a "Add an account" (1x, e.g. 103x17)
- small target: a "Add a user" (1x, e.g. 71x17)
- small target: a "Go to Single Sign-On" (1x, e.g. 139x17)
- small target: a "Add a customer" (1x, e.g. 104x17)

### notifications at 360px
- small target: input[checkbox] "email_exception_created" (1x, e.g. 20x20)
- small target: input[checkbox] "sms_exception_created" (1x, e.g. 20x20)
- small target: input[checkbox] "email_recommendation_awaiting_approval" (1x, e.g. 20x20)
- small target: input[checkbox] "sms_recommendation_awaiting_approval" (1x, e.g. 20x20)
- small target: input[checkbox] "email_account_unlocked" (1x, e.g. 20x20)
- small target: input[checkbox] "sms_account_unlocked" (1x, e.g. 20x20)
- small target: input[checkbox] "email_exception_auto_decided" (1x, e.g. 20x20)
- small target: input[checkbox] "sms_exception_auto_decided" (1x, e.g. 20x20)
- small target: input[checkbox] "email_deadline_reminder" (1x, e.g. 20x20)
- small target: input[checkbox] "sms_deadline_reminder" (1x, e.g. 20x20)
- axe label (critical): input[name="email_account_unlocked"]
- axe label (critical): input[name="email_deadline_reminder"]
- axe label (critical): input[name="email_exception_auto_decided"]
- axe label (critical): input[name="email_exception_created"]
- axe label (critical): input[name="sms_account_unlocked"]
- axe label (critical): input[name="sms_deadline_reminder"]
- axe label (critical): input[name="sms_exception_auto_decided"]
- axe label (critical): input[name="sms_exception_created"]
- axe label (critical): tr:nth-child(2) > td:nth-child(2) > input[type="checkbox"]
- axe label (critical): tr:nth-child(2) > td:nth-child(3) > input[type="checkbox"]

### notifications at 768px
- small target: input[checkbox] "email_exception_created" (1x, e.g. 20x20)
- small target: input[checkbox] "sms_exception_created" (1x, e.g. 20x20)
- small target: input[checkbox] "email_recommendation_awaiting_approval" (1x, e.g. 20x20)
- small target: input[checkbox] "sms_recommendation_awaiting_approval" (1x, e.g. 20x20)
- small target: input[checkbox] "email_account_unlocked" (1x, e.g. 20x20)
- small target: input[checkbox] "sms_account_unlocked" (1x, e.g. 20x20)
- small target: input[checkbox] "email_exception_auto_decided" (1x, e.g. 20x20)
- small target: input[checkbox] "sms_exception_auto_decided" (1x, e.g. 20x20)
- small target: input[checkbox] "email_deadline_reminder" (1x, e.g. 20x20)
- small target: input[checkbox] "sms_deadline_reminder" (1x, e.g. 20x20)
- axe label (critical): input[name="email_account_unlocked"]
- axe label (critical): input[name="email_deadline_reminder"]
- axe label (critical): input[name="email_exception_auto_decided"]
- axe label (critical): input[name="email_exception_created"]
- axe label (critical): input[name="sms_account_unlocked"]
- axe label (critical): input[name="sms_deadline_reminder"]
- axe label (critical): input[name="sms_exception_auto_decided"]
- axe label (critical): input[name="sms_exception_created"]
- axe label (critical): tr:nth-child(2) > td:nth-child(2) > input[type="checkbox"]
- axe label (critical): tr:nth-child(2) > td:nth-child(3) > input[type="checkbox"]

### notifications at 1024px
- axe label (critical): input[name="email_account_unlocked"]
- axe label (critical): input[name="email_deadline_reminder"]
- axe label (critical): input[name="email_exception_auto_decided"]
- axe label (critical): input[name="email_exception_created"]
- axe label (critical): input[name="sms_account_unlocked"]
- axe label (critical): input[name="sms_deadline_reminder"]
- axe label (critical): input[name="sms_exception_auto_decided"]
- axe label (critical): input[name="sms_exception_created"]
- axe label (critical): tr:nth-child(2) > td:nth-child(2) > input[type="checkbox"]
- axe label (critical): tr:nth-child(2) > td:nth-child(3) > input[type="checkbox"]

### notifications at 1280px
- axe label (critical): input[name="email_account_unlocked"]
- axe label (critical): input[name="email_deadline_reminder"]
- axe label (critical): input[name="email_exception_auto_decided"]
- axe label (critical): input[name="email_exception_created"]
- axe label (critical): input[name="sms_account_unlocked"]
- axe label (critical): input[name="sms_deadline_reminder"]
- axe label (critical): input[name="sms_exception_auto_decided"]
- axe label (critical): input[name="sms_exception_created"]
- axe label (critical): tr:nth-child(2) > td:nth-child(2) > input[type="checkbox"]
- axe label (critical): tr:nth-child(2) > td:nth-child(3) > input[type="checkbox"]

### setting_branding at 360px
- small target: input[color] "Accent color" (1x, e.g. 56x36)

### setting_branding at 768px
- small target: input[color] "Accent color" (1x, e.g. 56x36)

### setting_sod at 360px
- small target: a "Admin" (1x, e.g. 42x17)
- small target: a "Bookkeeper" (1x, e.g. 79x17)

### setting_sod at 768px
- small target: a "Admin" (1x, e.g. 42x17)
- small target: a "Bookkeeper" (1x, e.g. 79x17)

### setting_fraud_model at 360px
- table scrolls sideways: "Fraud scoring model" (287px in 278px)
- small target: a "Back to settings" (1x, e.g. 117x18)

### setting_fraud_model at 768px
- small target: a "Back to settings" (1x, e.g. 117x18)

### settings_dialog at 360px
- small target: button.settings-dialog-close "Close" (1x, e.g. 24x44)

### settings_dialog at 768px
- small target: button.settings-dialog-close "Close" (1x, e.g. 24x44)
