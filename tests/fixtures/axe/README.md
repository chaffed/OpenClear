# axe-core

`axe.min.js` is [axe-core](https://github.com/dequelabs/axe-core) 4.10.2 by Deque Systems,
under the Mozilla Public License 2.0 (its header carries the notice). It's used only by
`tests/test_web/test_accessibility.py` to check pages against WCAG 2.1 A and AA, and is
never served by OpenClear.

`baseline.json` lists the problems found when the check was added (UX_PLAN.md U10's
baseline audit). The test fails on any problem not in it, so new pages and changes can't
add any. Each UX wave fixes the problems on its own screens and removes them from the list.
