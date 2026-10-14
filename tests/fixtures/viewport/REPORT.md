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
| upload | ✓ | ✓ | ✓ | ✓ |
| approvals | ✓ | ✓ | ✓ | ✓ |
| customer | ✓ | ✓ | ✓ | ✓ |
| user_edit | ✓ | ✓ | ✓ | ✓ |
| user_new | ✓ | ✓ | ✓ | ✓ |
| users_bulk | ✓ | ✓ | ✓ | ✓ |
| security_groups | ✓ | ✓ | ✓ | ✓ |
| security_group_edit | ✓ | ✓ | ✓ | ✓ |
| check_return_reasons | ✓ | ✓ | ✓ | ✓ |
| ach_return_reasons | ✓ | ✓ | ✓ | ✓ |
| check_images | ✓ | ✓ | ✓ | ✓ |
| webhooks | ✓ | ✓ | ✓ | ✓ |
| webhook_new | ✓ | ✓ | ✓ | ✓ |
| api_client_new | ✓ | ✓ | ✓ | ✓ |
| sso_new | ✓ | ✓ | ✓ | ✓ |
| controls | ✓ | ✓ | ✓ | ✓ |
| changes | ✓ | ✓ | ✓ | ✓ |
| model_risk | ✓ | ✓ | ✓ | ✓ |
| ml_models | ✓ | ✓ | ✓ | ✓ |
| plugins | ✓ | ✓ | ✓ | ✓ |
| integration | ✓ | ✓ | ✓ | ✓ |
| layouts | ✓ | ✓ | ✓ | ✓ |
| billing | ✓ | ✓ | ✓ | ✓ |
| wizard | ✓ | ✓ | ✓ | ✓ |
| search | ✓ | ✓ | ✓ | ✓ |
| my_security | ✓ | ✓ | ✓ | ✓ |
| notifications | ✓ | ✓ | ✓ | ✓ |
| setting_branding | ✓ | ✓ | ✓ | ✓ |
| setting_password_policy | ✓ | ✓ | ✓ | ✓ |
| setting_reminders | ✓ | ✓ | ✓ | ✓ |
| setting_security_events | ✓ | ✓ | ✓ | ✓ |
| setting_sod | ✓ | ✓ | ✓ | ✓ |
| setting_support_access | ✓ | ✓ | ✓ | ✓ |
| setting_fraud_model | ✓ | ✓ | ✓ | ✓ |
| settings_dialog | ✓ | ✓ | ✓ | ✓ |
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

### users_bulk at 360px
- table scrolls sideways: "Bulk upload users" (372px in 278px)

### setting_fraud_model at 360px
- table scrolls sideways: "Fraud scoring model" (287px in 278px)
