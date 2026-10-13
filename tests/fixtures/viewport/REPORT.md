# Viewport baseline (MOBILE_PLAN.md R0)

Written by `tests/test_web/test_viewports.py` with `OPENCLEAR_VIEWPORT_WRITE_BASELINE=1`.
Each cell: sideways scroll (↔), small touch targets (kinds), Pay/Return below the fold (⬇), axe problems.

| Page | 360px | 768px | 1024px | 1280px |
|---|---|---|---|---|
| dashboard | ✓ | ✓ | ✓ | ✓ |
| exceptions | 3 small | 4 small | ✓ | ✓ |
| exception | ✓ | ✓ | ✓ | ✓ |
| issued_items | 1 small | 2 small | ✓ | ✓ |
| paid_items | 1 small | 2 small | ✓ | ✓ |
| stop_payments | ✓ | ✓ | ✓ | ✓ |
| ach_transactions | 1 small | 1 small | ✓ | ✓ |
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
| people | 1 small | 1 small | ✓ | ✓ |
| customer_control_panel | ✓ | ✓ | ✓ | ✓ |

## Details

### exceptions at 360px
- small target: input[checkbox] "Select Check ######" (3x, e.g. 24x24)
- small target: input[checkbox] "Select Check #####" (1x, e.g. 24x24)
- small target: input[checkbox] "Select Gusto Payroll (##########)" (1x, e.g. 24x24)

### exceptions at 768px
- small target: input[checkbox] "Select every item on this page that can " (1x, e.g. 24x24)
- small target: input[checkbox] "Select Check ######" (3x, e.g. 24x24)
- small target: input[checkbox] "Select Check #####" (1x, e.g. 24x24)
- small target: input[checkbox] "Select Gusto Payroll (##########)" (1x, e.g. 24x24)

### issued_items at 360px
- small target: a "####" (1x, e.g. 39x44)

### issued_items at 768px
- small target: a "####" (1x, e.g. 32x17)
- small target: a "#####" (42x, e.g. 42x17)

### paid_items at 360px
- small target: a "####" (1x, e.g. 40x44)

### paid_items at 768px
- small target: a "####" (1x, e.g. 33x17)
- small target: a "#####" (42x, e.g. 42x17)

### ach_transactions at 360px
- small target: a "Gusto Payroll" (6x, e.g. 44x34)

### ach_transactions at 768px
- small target: a "Gusto Payroll" (6x, e.g. 87x17)

### upload at 360px
- small target: a "Back to issued items" (1x, e.g. 149x18)

### upload at 768px
- small target: a "Back to issued items" (1x, e.g. 149x18)

### people at 360px
- small target: a "Manage" (1x, e.g. 53x17)

### people at 768px
- small target: a "Manage" (1x, e.g. 53x17)
