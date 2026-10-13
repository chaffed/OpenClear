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
| people | ✓ | ✓ | ✓ | ✓ |
| customer_control_panel | ✓ | ✓ | ✓ | ✓ |

## Details

### upload at 360px
- small target: a "Back to issued items" (1x, e.g. 149x18)

### upload at 768px
- small target: a "Back to issued items" (1x, e.g. 149x18)
