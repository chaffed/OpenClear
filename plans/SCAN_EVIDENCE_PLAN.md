# Scan evidence, kept private

The weekly ZAP active scan (`.github/workflows/zap.yml`) runs in a public repository, so it keeps
high-risk details out of its log and uploads no report. When CI finds something that doesn't happen
locally, nobody can see which request triggered it. That happened on 2026-10-26: a SQL injection
alert (rule 40018, 2 requests) and Backup File Disclosure (rule 10095, 3 requests) on the bank
administrator's pages. Neither reproduced in a local run of the same scan on the same commit.

## Decisions (the product owner, 2026-10-26)

- **SE-D1. Encrypt, then keep.** When the active scan finds anything of medium risk or worse that
  isn't waived, the workflow encrypts the full reports with `age` and attaches the file to the run
  (an artifact, kept 30 days). Anyone can download it; only the maintainers can read it. The issue
  it opens says which run holds the evidence.
- **SE-D2. Plain `age` in CI.** CI encrypts to one ordinary `age` public key, committed in
  `.github/scan-recipients.txt`, using `age` from Ubuntu's own packages (no download, no plugin).
- **SE-D3. The scan key lives behind a YubiKey, with a paper backup.** The scan key is stored only
  encrypted, to two recipients, either of which decrypts it alone:
  - **YubiKey 17027404** (YubiKey 5, firmware 5.4.3), the everyday key, through
    `age-plugin-yubikey` (by `age`'s author): its key is generated in a retired PIV slot and can't
    be exported; every use needs a touch, and the PIN once per session. PIV is separate from the
    key's FIDO2 credentials, so sign-ins are untouched.
  - **A paper key**, an ordinary `age` key shown once during setup, written down or printed, and
    kept in a safe apart from the YubiKey. It's saved nowhere.

  The work YubiKey, which carries a Commerce Bank certificate, isn't used. A Google Titan key would
  need `age-plugin-fido2-hmac`, which Homebrew doesn't carry, so it was set aside. A lost YubiKey,
  or the paper, is replaced by re-encrypting the scan key to the new pair; nothing CI encrypted
  needs redoing. A second YubiKey can be added as a third recipient later.
- **SE-D4. Reading a report:** `scripts/open_scan_evidence.sh <run id>` downloads the artifact,
  unlocks the scan key with the YubiKey (or the paper key), and decrypts into `.zap/`
  (git-ignored), never to disk in the clear elsewhere.
- **SE-D5. Dependencies.** `age` (CI, from Ubuntu's archive); `age-plugin-yubikey` and `ykman`
  (the maintainer's machine only). Why not the standard library or what we have: Python's standard
  library has no public-key encryption, and GPG would need its own key management; `age` is small,
  audited and maintained, and its plugin keeps keys on the YubiKeys.

## Stages

| Stage | What | Status |
|---|---|---|
| SE1 | The maintainer creates the scan key and wraps it to the YubiKey and a paper key (`scripts/scan_key_setup.sh`; the scan key is never written unencrypted) | Done |
| SE2 | The workflow: encrypt and attach on findings; the issue names the run | Done |
| SE3 | `scripts/open_scan_evidence.sh`; SECURITY.md, ADR 0002 and CLAUDE.md mention it (it's for maintainers, so not the runbook, which is the service provider's) | Done |

## Progress log

- 2026-10-26: Plan written. The `needs-triage` and `needs-info` labels created, so the scan files
  its findings instead of failing; the active scan re-run by hand on `main` (a9f2d5e).
- 2026-10-26: The re-run on GitHub (a9f2d5e) found nothing of medium risk or worse, nor did a local
  run of the same scan on 3e2be93: the morning's alerts didn't recur, most likely a slow response on
  that runner, unproven without the report. Key choice: YubiKey 17027404 and a paper backup.
- 2026-10-26: SE1–SE3 built. The maintainer ran `scripts/scan_key_setup.sh 17027404`: the scan key
  (public half `age1nzanrlx…u8y` in `.github/scan-recipients.txt`) is in `.github/scan-key.age`,
  encrypted to the YubiKey (`piv-p256`) and the paper key (`X25519`). The ZAP workflow encrypts
  `.zap/` to it and keeps it as the `zap-evidence` artifact for 30 days when the active scan finds
  anything; the issue names the run. `actions/upload-artifact` is pinned to v7.0.2's commit.
