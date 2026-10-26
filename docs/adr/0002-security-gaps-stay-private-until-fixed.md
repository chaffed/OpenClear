# Security gaps stay private until they're fixed

The repo is public, and OpenClear's issues, plans and compliance pages are all public. An
exploitable finding is the exception: it's handled as a private GitHub security advisory and
published only once a release fixes it. A finding that can't be exploited (missing
documentation, a gap in the pipeline) is an ordinary issue labelled `security`. The ASVS
crosswalk is published only when its remaining "Not met" rows are all of that second kind.

Publishing the assessment in full was rejected because a "Not met" row about something
exploitable tells an attacker where to look, and banks in production can't patch faster than
an attacker reads. Keeping everything private was rejected because banks' reviewers need the
crosswalk itself, not a promise under NDA.

The weekly ZAP active scan runs in public too. Its log and the issue it opens name no vulnerable
request; the full reports are kept with the run, encrypted to the maintainers' scan key, which only
a YubiKey or a paper key in a safe can unlock (plans/SCAN_EVIDENCE_PLAN.md). Anyone can download
them; nobody else can read them.

Reports come in through GitHub private vulnerability reporting. There's no `security@`
address until OpenClear has a domain of its own.
