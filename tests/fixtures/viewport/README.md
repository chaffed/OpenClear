# Viewport checks (MOBILE_PLAN.md R0)

Used by `tests/test_web/test_viewports.py`, which opens the key pages at 360, 768, 1024 and
1280px in Chromium and checks, at each width:

- **Sideways scroll**: the page is no wider than the screen (a table inside `.table-scroll`
  may scroll on its own).
- **Touch targets** (below 1024px): links, buttons, fields, `<summary>` at least 44×44px. A
  checkbox or radio counts as the size of its label; a link inside a sentence is exempt
  (WCAG 2.5.8's exception).
- **Decision buttons** (the exception page): Pay and Return on screen without scrolling.
- **axe**: WCAG 2.1 A and AA, as in `test_accessibility.py`.

Files:

- `baseline.json`: the problems found when the check was added, by page and width. The test
  fails on any problem not in it. Rows of the same kind count once (digits become `#`).
- `REPORT.md`: the same baseline as a table and a list, to read.
- `darwin/*.png`: each page at 1280px, the desktop reference. Compared only on macOS with
  SQLite (fonts differ on Linux; Postgres orders rows made in the same second differently).
  Times and dates are replaced by fixed text before the picture is taken. A changed picture
  fails the test, with the new picture and the difference in `.openclear-run/viewport-diffs/`.

When a phase fixes problems, or the desktop is deliberately changed, rewrite all three:

```
OPENCLEAR_VIEWPORT_WRITE_BASELINE=1 .venv/bin/python -m pytest -q tests/test_web/test_viewports.py
```

(`OPENCLEAR_VIEWPORT_SHOTS=1` also saves every page at every width in
`.openclear-run/viewport-shots/`, to look at) and check in the diff: the baseline should only shrink, and a changed picture should be one
you meant to change.
