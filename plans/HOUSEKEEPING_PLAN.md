# Housekeeping

Small things left over after every plan's buildable work shipped (1.32.0, 2026-10-29): an open
code-scanning alert, an ASVS status that the code has already overtaken, a test-speed stage left
"In review", repository clutter, and the image's size. None is a feature; each either makes the
security record a prospect reads more accurate or stops a recurring chore.

Written 2026-10-30 at the product owner's request.

## What's there

| Item | Found | Why it matters |
|---|---|---|
| **CodeQL alert #176** (medium, "Information exposure through an exception"), open since 2026-10-20 | `web/routers/tenant_settings.py`: marking an outside service reviewed catches `ValueError` and shows `str(exc)` to the person. The only `ValueError` raised there today is OpenClear's own sentence ("That isn't a service OpenClear uses for you."), so nothing leaks now, but anything deeper that raises `ValueError` later would show its text. | It doesn't fail CI (only high and critical do), but it's the one open alert on the repository's security page, which a bank's security team reads. |
| **ASVS 2.3.4** ("business-logic locking for limited resources") marked *Partly*: "Locking when two people decide the same item at once is to be verified." | FR1 built the per-item lock in 1.27.0, and `tests/test_postgres/test_decision_state_concurrency.py` proves it: two decisions at once, one wins and the other is told. FR2 and FR3 added the same for a check presented twice and the decision outbox. None of those tests carries the `asvs("2.3.4")` mark. | The release notes and security-metrics page quote the ASVS count (214 of 242 met). This one is met and evidenced, just not recorded. |
| **TEST_SPEED_PLAN.md TS4** left "In review" | The progress log records the work (the Redis limiter's 11 s retry, which also held production requests while Redis was down; the Tesseract thread limit); the stage was never closed. | A plan that looks unfinished when it isn't. |
| **The main checkout** (`/Users/stephen/PosPay`) | On `fr1-item-lock` from 2026-10-21, with `docs/compliance/security-metrics.md` changed: my own mistake at the 1.31.0 release, when `scripts/release_metrics.py` ran in this checkout instead of the release worktree. `main` already has the right page. | The local test runner (`scripts/run_tests.sh`) and anyone opening the folder start from a stale branch with a confusing change in it. |
| **Branches** | 118 local branches (111 merged into `main`); 39 on GitHub (38 merged, and `fr1-item-lock`, whose changes are all on `main` by patch). Two worktrees are stale: `.openclear-wt-tests` (detached) and a prunable one in the old scratch folder. | Clutter; and GitHub doesn't delete a pull request's branch when it merges, so it keeps growing. |
| **Two backup branches**, `backup/pre-clean-main` and `backup/pre-clean-billing` (local only, 2026-10-08) | Made before the history clean-up. They hold six commits `main` doesn't, among them "Ignore /business/ (private planning documents)" and the billing plan's research. | They may be the only copy of what the clean-up removed, so deleting them isn't mine to decide. |
| **Five more unmerged local branches**: `fast-tests`, `http-errors`, `m3-deciding`, `x937-standard` (nothing unique by patch), `scale-s3` (one commit unique by patch, the S3 job queue, whose work is on `main` in a different shape) | Old feature branches merged by rebase or squash. | Safe to delete once confirmed. |
| **The image is 2.4 GB** | It was 450 MB at 1.12.0. The inventory (`docs/compliance/third-party.md`) shows what grew: numpy, scipy, pandas and scikit-learn (fraud scoring), the Azure, AWS and Twilio clients, and WeasyPrint's libraries. The Dockerfile installs every runtime extra on purpose, "so one image works everywhere". | Slower deploys and pulls, more for a scanner to report. A decision, not a fix: see HK-D4. |

## Decisions (recommendations; the product owner's to confirm)

| Id | Question | Recommendation |
|---|---|---|
| HK-D1 | Fix the code behind #176, or dismiss the alert? | **Fix it.** Show a fixed sentence instead of the exception's text, and give the outside-services module its own error type, so the code says which errors are meant for people. Dismissing would need the same reasoning written down and would come back with the next similar change. |
| HK-D2 | Delete merged branches, here and on GitHub? | **Yes:** the 111 merged local branches, the 38 merged GitHub branches and `fr1-item-lock`, the five old feature branches, and the two stale worktrees. Turn on GitHub's *Automatically delete head branches*, so merged pull requests clean up after themselves. Deleting a merged branch loses nothing: its commits are on `main`. |
| HK-D3 | The two `backup/pre-clean-*` branches? | **Keep them, but not in this repository.** Bundle them (`git bundle`) into a file kept wherever the private planning documents are kept, then delete the branches. Or keep them as they are if you'd rather; they're local only and never pushed. |
| HK-D4 | The image's size? | **Measure first, decide after.** HK5 records what each layer and each extra costs. The likely options are a slim image without the cloud OCR and SMS clients beside the full one, or leaving it, since the full image is what "one image works everywhere" asked for. No change without your decision. |

## Stages

| Stage | What | Status |
|---|---|---|
| HK1 | CodeQL #176: an `OutsideServiceError` for the outside-services module and a fixed message in the router; a test that the person sees the sentence, not an exception's text. The alert closes when CodeQL next scans `main`. | |
| HK2 | ASVS 2.3.4: `@pytest.mark.asvs("2.3.4")` on the tests that prove the locks (two decisions at once; a check presented twice; the outbox writing a batch once); the status to *Met* with that evidence in `docs/security/asvs-status.csv`; `scripts/asvs_crosswalk.py`. | |
| HK3 | TEST_SPEED_PLAN.md: TS4 closed, with the suite times now (CI and local), so the plan is done. | |
| HK4 | The main checkout back on `main` with the stray change discarded; the stale worktrees removed; merged and old branches deleted locally and on GitHub; the backup branches as HK-D3 decides; GitHub's automatic branch deletion turned on. | |
| HK5 | The image's size: each layer and each runtime extra measured, written up here with the options. | |

HK1 to HK3 are one pull request. HK4 changes no files in the repository; it needs approval for
each deletion and the setting (HK-D2, HK-D3). HK5 is a write-up only.

## Progress log

- 2026-10-30: Plan written after the third-party plan finished (PRs #63–#65). Waiting for the
  product owner's decisions HK-D1 to HK-D4.
