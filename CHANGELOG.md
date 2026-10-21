# Changelog

## Unreleased

- **Deciding is safe when two people act at once** (`FULL_REVIEW_PLAN.md` FR1). Every change to an
  item's decision takes the item's lock first, so a recommendation made the moment someone else
  decides is told the item is decided, instead of leaving it waiting for an approval that could
  never be given; and a decision taken back at the cut-off isn't sent. The default decision at
  the deadline never overrides someone deciding at that moment.
- **Decisions reach the bank within a minute of the cut-off.** They're locked and sent every
  minute (`OPENCLEAR_DECISION_LOCK_INTERVAL_SECONDS`, 60), each on its own, so one that fails is
  retried without holding the rest back. They used to wait for the five-minute deadline sweep.
- **A check presented twice at the same moment is paid once** (FR2): the second presentment waits
  for the first and is caught as a duplicate. Two decision files can no longer carry the same
  decisions (a scheduled run and an API request at once), and two people approving a settings
  change at once apply it once. Reset, email-change and invitation links, and online banking
  launch codes, work exactly once even when opened twice together. A background import handed
  to another worker (its heartbeat looked stale) can no longer commit alongside it.
- **Sign-in** (FR3): every wrong password counts even when many arrive at once, and a wrong
  current password on the change-password form, or on accepting an invitation, now follows the
  bank's own lockout rule (attempts, minutes, or until unlocked) like signing in does. After a
  lockout runs out, the next wrong password locks it again. Password sign-ins are limited per
  address too (`OPENCLEAR_LOGIN_ATTEMPTS_PER_MINUTE`, 20), web, API and platform.
- **Refresh tokens are used once** (FR3). Each refresh returns a new refresh token (as before);
  the one used is retired. Presenting a retired one more than a minute later ends the session and
  raises a high-severity `auth.session.reuse_detected` security event. **API change:** clients must
  keep the refresh token each refresh returns; one that refreshes with its first token again is
  signed out.
- API: an `Idempotency-Key` reused for a different person (`OpenClear-Act-As`) is refused as a
  different request instead of replaying the first person's answer.

## 1.26.0 — 2026-10-21

- Internal (`REVIEW_FIXES_PLAN.md`): every redirect builds its query in one place, so messages
  after an action are encoded the same way everywhere (a space is now `+` in the address).
  Release metrics now count the release's own advisory. No change to what pages show.
- **Readable in dark mode with any bank colour** (`UI_PLAN.md` UI1): text, links and focus
  rings in a bank's accent use a shade of it that reaches WCAG AA contrast in each theme. The
  bank name and links were too dark to read on the dark theme before. Links all share that
  colour, and the Branding setting previews it in both themes.
- **Deciding moves on to the next item** (`UI_PLAN.md` UI2): after Pay or Return, the next item
  still to decide opens, with a note of what was just done; after the last, the list says
  nothing else is waiting.
- The "Decide now" link is no longer hidden behind the theme and Help controls. The theme
  choice moved into the navigation, which also gains **Home**.
- A customer's Getting Started checklist: "Add an account" and "Add a user" open with the
  customer already chosen, and saving comes back to the checklist. Steps say "Done" or "To do".
- A new stop payment is effective today unless changed, and a form with an error keeps what was
  typed.
- **Decisions can be changed until the cut-off** (`UI_PLAN.md` UI3, the product owner's
  decision). Each bank sets a decision cut-off per kind of item (checks, ACH) in its time zone,
  with a minimum review time; a customer can have its own cut-off, agreed with the bank. Items
  are due at the first cut-off on a business day (weekends, Federal Reserve holidays and the
  bank's closed days skipped). Until then a decision can be changed or taken back (under dual
  control, a change is recommended and someone else approves it); at the cut-off it becomes
  final. Every earlier version is kept.
- **Integrations: decisions now reach the decision feed, the `exception.decided` webhook and
  decision files at the cut-off, not when they're made.** Every bank has a cut-off: until it sets
  its own, the installation's default, 11:00 New York time (`OPENCLEAR_DEFAULT_DECISION_CUTOFF`,
  `OPENCLEAR_DEFAULT_TIME_ZONE`; production won't start without one). The API's decisions gain
  `locked_at`, `changeable_until` and `change_pending`, and its exceptions `decision_final` and
  `changeable_until`: act only on a final decision, or take decisions from the feed, the webhook or
  decision files. `PUT` and `DELETE /api/v1/exceptions/{id}/decision` change or take back a
  decision before its cut-off.
- A customer's home page leads with its two jobs: what's left to decide (or that nothing is),
  and uploading issued checks, with the last file's result. An account menu under your email
  holds Security, the theme and Log out; "Switch organization" shows only if you belong to
  another. The upload page starts with the file, with the format reference folded away.
- Phones show a number keyboard for amounts and check and account numbers. Sign-in works with
  password managers, asks for the "Organization" in plain words (and not at all on a bank's own
  sign-in link), and says what to do about a forgotten password.
- A bank's home page lists the five items due soonest, with their customers. The Controls tile
  says "1 failing" or "2 warnings", and Getting started shows how far along it is.
- **A bank can invite a customer's people by email, with no access to start with.** From the
  customer's page (and its Getting Started checklist): they choose their own password and can look
  after their own sign-in, nothing else, until someone other than whoever invited them gives them
  a role under Users. Navigation shows only what a person may open.
- The footer's license notice is one line, with the copyright, the no-warranty statement and a
  link to the source code (`OPENCLEAR_SOURCE_CODE_URL`, for an installation running its own
  changes); the license page opens with the full notice.
- Section headings are smaller than the page title, as they should be; secondary buttons are
  outlined rather than grey (which looked disabled); "Doesn't match" sits with the row it's about.
- An upload's result leads with what happened ("1 issued check added. 1 row needs fixing."),
  lists the checks it added, and keeps "Back out this upload" apart from the other buttons.

## 1.25.1 — 2026-10-19

- **Security (high, [GHSA-55j4-4cj6-8j2f](https://github.com/chaffed/OpenClear/security/advisories/GHSA-55j4-4cj6-8j2f)):**
  an attestation's evidence file whose name contained `../` was written outside the storage
  folder, with local file storage. Anyone who may attest could do it, including a business
  customer's administrator. Storage now refuses any key that isn't a plain path inside its area,
  for every kind of file, and uploaded names keep only their last part. Affects 1.19.0 to 1.25.0.
- A security key that fails to register or sign in now says so plainly; the technical detail
  goes to the server's log instead of the page.
- Every redirect goes through one check that keeps it on the site (the three to a bank's own
  identity provider excepted), and cookies are only ever set again with values shaped like
  OpenClear's own.
- Code scanning: the open alerts resolved (`CODE_SCANNING_PLAN.md`), Bandit's findings uploaded
  from medium severity, and CodeQL now fails the build on an open high or critical alert.

## 1.25.0 — 2026-10-19

Files and verification (OWASP_PLAN.md OW4):

- **Malware scanning of uploads**, optional, off until the service provider turns it on. ClamAV
  scans every file uploaded through the web, the API or the drop folder; VirusTotal is asked
  about each file's SHA-256 (never sent the file). Malware is refused, or quarantined if found
  later, with a high-severity `file.malware_detected` security event, an action-log entry and an
  email to the bank's administrators. New settings: `OPENCLEAR_MALWARE_CLAMD_ADDRESS`,
  `OPENCLEAR_MALWARE_VIRUSTOTAL_API_KEY` and their `_ON_ERROR` choices. A new CUEC (B-SI-3-01):
  the bank's own malware detection.
- **Account numbers stay out of URLs.** A list's search is posted, and its address carries the
  matching accounts' ids instead of the number typed; the search box in the header is posted
  too. Old links with `?q=` still work.
- **Check-image views are in the action log**, once per person, image and side an hour.
- **Verification:** an OWASP ZAP baseline scan on every change (signed in as a bank
  administrator and a customer user), a weekly active scan, the WAF exclusions proven against the
  OWASP Core Rule Set at paranoia levels 1 and 2 (`scripts/edge_check.py --crs`,
  `docs/guides/crs/openclear-exclusions.conf`), and a security checklist in the pull request
  template.

## 1.24.0 — 2026-10-18

Sessions and identity (OWASP_PLAN.md OW3c):

- **Where you're signed in:** the Security page lists your sessions (browser, address, when),
  and you can sign out any one of them. An administrator sees a person's sessions on their page,
  and can **sign everyone out** at once (Users, or People for a customer's administrators),
  except themselves.
- **New-device emails:** a sign-in from a browser you haven't used before emails you, always.
  Your first browser after upgrading is recorded quietly.
- **Email addresses can change.** You sign in with your email, but you are your account, not
  your address. From Security, after a recent sign-in, you can ask for a new address; a link to
  it confirms the change. An administrator can do the same for someone who belongs only to their
  organization. The bank's directory (SCIM) changes it directly, and sign-on moves a known person
  to a new address in one of the bank's or customer's verified domains. Every change emails the
  old and the new address.
- **A bank can verify its own email domains** (Single Sign-On page), as customers already can.
- **Move everyone to a new domain** after a rename: everyone at the old domain moves to the
  same name at a verified new one, once a second administrator approves. Anyone who also
  belongs to another organization is sent a link to confirm it themselves.

## 1.23.0 — 2026-10-18

Required security keys and the ASVS crosswalk (OWASP_PLAN.md OW3b):

- **A bank can require a security key or passkey for password sign-ins** (Settings → Required
  security keys), and a customer can for its own people. People without one get a grace period
  (14 days unless set otherwise, at most 90) with a reminder, then add one as part of signing in.
  SSO and online banking sign-ins aren't affected. Turning it off waits for a second admin.
- **An administrator can reset a person's security keys** when they lose one, after saying how
  they confirmed it's really them; the person is signed out and emailed. If a bank's only
  administrator loses theirs, two of the service provider's operators can reset it.
- **Platform staff always sign in with a security key**, registered at their first sign-in.
- **An SSO connection can require its provider to report MFA**, refusing sign-ins that don't.
- **A bank can require its API clients to sign with a key pair**, refusing secrets.
- With a key required, the password policy page says NIST allows an 8-character minimum.
- **Removed, as announced in 1.22.1:** the old cookie names over HTTPS, and tokens without an
  issuer and audience. Anyone still signed in from before 1.22.1 signs in again.
- **Documentation:** OpenClear against OWASP ASVS 5.0 (`docs/compliance/asvs.md`, every
  requirement with its status and evidence), and `docs/security/` pages on authentication,
  authorization, cryptography, and inputs and limits; a log inventory in the SIEM guide; two new
  CUECs for second factors at the identity provider and in online banking.

## 1.22.1 — 2026-10-18

Security fixes from the ASVS assessment (OWASP_PLAN.md):

- **Every character of a password counts.** bcrypt only reads 72 bytes, so passwords are now
  pre-hashed (HMAC-SHA-256) first. Existing hashes keep working and are upgraded at the next
  sign-in.
- **Single sign-on matches people by the provider's subject**, not only their email: a sign-in
  whose subject and email disagree with what OpenClear knows is refused until an
  administrator checks (`auth.sso.failed`). SAML connections with transient NameIDs still
  match by email.
- **Tokens name their issuer and purpose**, so a token from another installation, or signed for
  another use, is refused. Tokens issued before this release still work until they expire.
- **Cookies use `__Host-` and `__Secure-` prefixes over HTTPS.** Nobody is signed out by the
  upgrade: the old names are still read until 1.23.
- **Signing out clears the browser's cache and storage** (`Clear-Site-Data`).
- **Production defaults:** the interactive API pages (`/docs`, `/redoc`) are off unless
  `api_docs_enabled` is set (the API schema stays at `/openapi.json`). Production refuses to
  start with `siem_tls_verify` false, and SIEM delivery requires TLS 1.2 or newer. Text logs
  escape line breaks, and production warns when logs aren't JSON.
- **New SAML connections get RSA-3072 signing keys.** Existing ones keep theirs for now.
- The release feed no longer follows redirects.

## 1.22.0 — 2026-10-18

Passwords, resets and security keys (OWASP_PLAN.md OW3a):

- **Breached and guessable passwords are refused.** Every new password is checked against the
  100,000 most common breached passwords (bundled, stored only as hash prefixes). An
  installation can add Have I Been Pwned's full downloadable list (`breached_password_file`,
  searched in place) or its range API (`breached_password_hibp`). Passwords also can't contain
  OpenClear, the bank's or customer's name, or the person's email address.
- **New banks require 15 characters.** Existing banks keep their setting.
- **Passwords are re-checked at sign-in.** Someone whose password is now too short, or has
  appeared in a breach, signs in but must choose a new one before going further
  (`auth.password.weak` security event).
- **An administrator's password reset now emails the person a one-time link** to choose a new
  password, valid for 24 hours (`password_reset_link_hours`). The administrator no longer sees
  a temporary password. The old password stops working at once. Resetting needs email to be
  configured.
- **Adding or removing a security key needs a sign-in within the last 10 minutes**
  (`recent_sign_in_minutes`), on the web and the API, and the person is always emailed about
  it. Both are now audited (`auth.mfa.added`, `auth.mfa.removed`).
- **Sign-in gives the same answer for a wrong password, an unknown email and a locked
  account**, and takes the same time. **API change:** `POST /api/v1/auth/login` answers a
  locked account with `401` (it was `423`). The account's owner is still emailed when it locks.
- **`/.well-known/security.txt`** is served once `security_txt_contact` is set.
- **`SECURITY.md`** says how to report a vulnerability in OpenClear (GitHub private reporting).

Supply chain and security documentation (OWASP_PLAN.md OW1, OW2):

- **Dependencies install only from hashed lock files** (`requirements/runtime.txt` for the image
  and the launcher, `requirements/dev.txt` for development and CI), written by `scripts/lock.sh`.
  The Docker image now includes every runtime extra that needs no system libraries (Postgres,
  S3, Azure, Redis, SMS, cloud OCR, PDF), so one image works everywhere; `EXTRAS` now only adds
  `mssql` or `opencv`, unlocked. Source installs: `pip install --require-hashes -r
  requirements/dev.txt && pip install --no-deps -e .`
- **CI gates:** known-vulnerable dependencies with a fix (`pip-audit`, with expiring waivers),
  a stale lock, high-severity Bandit findings, and secrets (gitleaks) fail the build; CodeQL
  analyses the server and SDKs. Actions are pinned to commits and base images to digests.
- **An SBOM (CycloneDX) is attached to every release**, including the vendored JavaScript.
- **Security documentation:** a risk profile and data classification, a threat model kept as
  code (OWASP pytm), and a SAMM self-assessment, in `docs/security/`.

## 1.21.2 — 2026-10-17

Security fix for people who belong to more than one organization:

- **Switching organization now respects how you signed in.** A switch reuses the current
  sign-in, so it's allowed only where that sign-in would have been accepted. A password
  session can't switch into an organization that has turned password sign-in off. A session
  from a bank's single sign-on stays within that bank. A session opened from online banking
  can't switch at all. In each case the person signs in to the other organization directly.
- **Switching into a membership that requires a security key needs a key already registered
  there.** Registering a first key now happens only when signing in to that organization
  directly.
- **"Sign out other devices" keeps the session's limits.** The re-issued session used to lose
  them, so a session confined to one customer could then switch organization.
- Sessions started before this release can't switch until their next sign-in.

## 1.21.1 — 2026-10-17

Finishing dual control (DUAL_CONTROL_PLAN.md DC6):

- **Platform API keys wait for a second operator.** `scripts/create_metering_api_key.py` prints
  the key once; it works when another operator approves it under Platform → Changes (only its
  hash waits). `--emergency` makes it work at once, as a critical security event.
- **The provider's action log is checked automatically**, daily and whenever an operator opens
  Platform → Changes, and every bank's Controls page shows it ("Service provider's action log
  intact"). A broken log is a critical `audit.chain.verify_failed` security event.
- **The evidence export carries the provider's side**: its log's last verification and the
  installation settings' digest against what an operator last acknowledged.
- **Administrators are told when a rule loses its last other approver**, so its changes now
  take the 24-hour route.
- **"Sensitive settings need a second admin" is its own check**, apart from "Someone else can
  approve" (control inventory 1.3).
- **A setting with a change waiting says so** at the top of its form.
- Turning off the attestations' second person, and pooling decisions into the shared model,
  now raise a `settings.changed` security event.
- Platform → Changes reads as cards on phones.

## 1.21.0 — 2026-10-17

Two people for settings changes (dual control for settings; DUAL_CONTROL_PLAN.md):

- **Turning a protection off or down always needs a second admin**, at every bank, from this
  release. That covers segregation of duties (*enforce* to *warn*), dual control on decisions,
  the second-person switches, lockout, password and inactivity rules, the attestations' second
  person, approval of new customer users, required sign-on and password sign-in, and security
  event forwarding. Tightening any of them applies at once. **Moving segregation of duties to
  *warn* is the change you're most likely to notice.**
- **Three switches** under Settings → Dual control, each turned on only while someone else
  can approve:
  - *Sensitive settings need a second admin*: rules, models, security groups, API clients,
    webhooks, single sign-on, mappings and SCIM tokens, decision files, the embedded view,
    customer self-service, and switching to the shared model. It's on for banks that were on
    *enforce*, which is what used to send rule and model changes for approval. New API
    clients, webhooks and sign-on connections are made inactive with their secret shown once,
    and approval turns them on.
  - *Access changes need a second admin*: new people, wider permissions or accounts, and
    reactivation. Removing access never waits.
  - *Customers' sensitive changes need a second person*: a self-managing customer's sign-on,
    API clients and new administrators. Another of its administrators approves, or the bank's
    staff when the customer has only one. A customer can turn this on for itself.
- **Changes** shows every waiting change with its values before and after. You can approve,
  reject (with a reason) or withdraw it, and the API has the same in `/api/v1/changes`. A
  change answers `202` when it waits. Approvers are told by email or text, and there are
  webhooks `change_request.created` and `change_request.decided`. A change expires after
  seven days, is refused if the setting changed meanwhile, and only one waits per setting. If
  nobody else can approve, it applies alone after 24 hours. That raises a critical
  `change_request.applied_alone` security event.
- **At the service provider**, these wait for a second operator (Platform → Changes):
  provisioning a bank, platform roles (`scripts/platform_staff.py`; `--emergency` applies at
  once and is flagged), fee items, and activating the shared model. Host attestations are
  prepared by one operator and accepted by another. The provider has its own signed,
  hash-chained action log with a `platform.action` security event. Changes to the
  installation's settings show on the Controls page until an operator acknowledges them.
- **Controls** (inventory 1.2):
  - OC-AC-07 becomes "Two people for settings changes". It checks every protected change and
    warns when a bank with a second admin hasn't turned on the sensitive switch.
  - New OC-AC-13 "Two people at the service provider".
  - The evidence export lists the period's changes with both people's names and the values.

Lockout and inactive accounts (Settings → Lockout and inactive accounts):

- **A bank sets its own lockout**: how many failed password sign-ins lock an account and for how
  long (by default the installation's 5 and 15 minutes), or locked until an administrator
  unlocks it.
- **Deactivate inactive access** after a number of days without a sign-in to the bank, and
  **remove** it after a number of days, each with its own switch (both off by default). People
  are told a week before; API clients and the platform's staff are never touched; the bank's
  last active administrator is always kept; a removed person's name stays on everything they
  did. Each is in the action log and a security event. Runs daily with housekeeping.
- Each membership keeps its own last sign-in (`last_active_at`), and the dormant-account checks
  use it and say when the bank's rule is on. OC-AC-02 and OC-IA-01 say so too (control
  inventory 1.1).

- **SOC 2 (the 2017 Trust Services Criteria) for every control and CUEC**, from AICPA's mapping
  to NIST SP 800-53 Rev. 5, which OpenClear doesn't redistribute: download it from AICPA, run
  `scripts/import_crosswalks.py soc2` on it, and set `OPENCLEAR_SOC2_MAPPING_PATH`. Then a `soc2`
  field in `GET /api/v1/compliance/controls` and `/cuecs` (null without a mapping), the criteria
  on each CUEC's page, in the attestations CSV, and as `soc2-criterion` properties in the OSCAL
  component definition. See "Connecting your GRC tool".

## 1.20.0 — 2026-10-17

- **A segregation-of-duties breach that *enforce* refused is now a `critical` security event**
  (it was `high`, as a breach *warn* lets through still is), as the control testing
  specification asks.
- `OPENCLEAR_CONTROL_TESTS_INCLUDE_SANDBOXES`: include sandbox banks in scheduled control testing
  and attestation proposals (off by default; the public sandbox turns it on).

- **A new check, "Software kept up to date"**, for the host's CUEC to apply updates: it reads
  OpenClear's published releases (`OPENCLEAR_RELEASE_FEED_URL`, GitHub's releases API by
  default, or a mirror; empty turns it off) and warns when a newer release has been out more
  than 30 days (`OPENCLEAR_SOFTWARE_CURRENCY_WARN_DAYS`), and fails after 180
  (`OPENCLEAR_SOFTWARE_CURRENCY_FAIL_DAYS`). The feed is read at most every 12 hours. It was
  the last check the CUEC inventory listed as planned.

Customers' own CUECs (CONTROLS_PLAN.md CT-D13):

- **The bank sees its customers' attestations**: Settings → CUECs lists each customer with how
  many of its own CUECs it attested for last year, how many wait to be accepted, and how many of
  its people can attest (`GET /api/v1/compliance/customers`).
- **Ask customers to attest each year** (off by default, Settings → CUECs): once a year ends,
  each customer's people who can attest are asked by email (or SMS, as they choose) to attest
  their responsibilities for it; or ask them now. Recorded in the action log.

- The README, the compliance page, the GRC guide and the setting's own description now say what
  `OPENCLEAR_ENABLE_CONTROL_TEST_SCHEDULER` does: it runs all scheduled control testing (the
  hourly and nightly checks, the daily anomaly capture) and proposes each period's attestations,
  not only the anomaly capture. Without it, automated CUECs are attested "not tested".

## 1.19.0 — 2026-10-17

Compliance for GRC tools (CONTROLS_PLAN.md CT5):

- **An API for GRC tools**, under `/api/v1/compliance/`: each control's test results and
  deviations for a period; CUEC mappings (list, set a CUEC's mappings, load the filled-in
  `cuecs.csv`); the evidence behind a CUEC for a period, with the result OpenClear would propose;
  attestations recorded (with an evidence file), accepted, rejected, listed and pulled signed,
  filtered by your own control id. Lists page as the others do, and with `updated_since` return
  only what changed. A read-only API client with `compliance:read` can pull everything; a
  customer's own client sees only its own.
- **OSCAL 1.2.1**: OpenClear's controls and CUECs as a component definition, and a bank's period
  as assessment results (an observation per check, a finding per deviation, each accepted
  attestation), checked against NIST's published schemas and read by compliance-trestle.
- **Exports**: a period's attestations as signed JSON, CSV or OSCAL
  (`GET /api/v1/compliance/attestations/export`), and the evidence export now carries the
  period's control testing: each check's results, the deviations and the attestations.
- **CLI**: `openclear compliance controls`, `cuecs` and `attestations` (`--format`); the SDKs
  have every call.
- **A guide, "Connecting your GRC tool"** (`docs/guides/grc.md`): mapping, the pull and push
  patterns, verifying signatures, and OSCAL.
- CUEC mappings, attestations and control deviations have an `updated_at` (migration
  `d5f7a9b1c3e4`).

NIST CSF 2.0 crosswalk (CONTROLS_PLAN.md CT6):

- **Every control and CUEC now lists its NIST CSF 2.0 subcategories**, taken from NIST's own
  mapping of CSF 2.0 to SP 800-53 Rev. 5.1.1 (the informative references in NIST's CSF 2.0
  Reference Tool), not judged by hand. They're in `GET /api/v1/compliance/controls` and `/cuecs`
  (a new `csf` field), in the published inventories (`docs/compliance/controls.md`, `cuecs.md`,
  `.json`, and a `csf` column in `cuecs.csv`), and on each CUEC's page.
- **A new page, NIST CSF 2.0** (`docs/compliance/csf.md`, on the docs site), lists OpenClear's controls and the
  CUECs by CSF subcategory.
- The **FFIEC page** names the OpenClear controls behind each theme, with their NIST SP 800-53
  controls.

## 1.18.0 — 2026-10-16

Control testing (CONTROLS_PLAN.md CT2):

- **25 new checks**, from data OpenClear already keeps, bringing the Controls page to 38: dormant
  staff and customer accounts, privileged accounts, staff's and customers' second factor, customers
  who both issue and decide, customers' API client secrets, their own sign-on, directories in step,
  integrations over HTTPS, the model-pooling acknowledgement, default dispositions, uncorrected
  upload rows, stale ACH authorizations, unsigned unauthorized-debit statements, the quarter's
  evidence export, scheduled jobs running, HTTPS and HSTS, platform staff's second factor, and
  whether deviations are dealt with.
- **Population checks** over every item in a period: dual control on every decision, rule and
  model changes approved by a second admin, a replay before each change, support access granted
  with care, and customers deciding in time. They run nightly over the previous day, and any past
  period can be tested on demand (Controls → Deviations → Test a past period).
- **Test history**: every scheduled run of every check is kept (a page view keeps only a change),
  with its evidence and its SHA-256, for seven years by default. Each check has a page with its
  last 30 days as a strip, its results and its deviations.
- **Deviations**: a FAIL (or a WARN, for the checks you choose) opens one, and a return to PASS
  closes it. Record the cause and your response (new permission `compliance:manage`); set the
  grace period after which an open one fails the "Deviations dealt with" check.
- Each scheduled job's last run is now recorded, for the scheduled jobs check.

Access reviews and the last checks (CONTROLS_PLAN.md CT3):

- **Access reviews** (Settings → Access reviews): the bank reviews every membership (staff,
  customers' people, API clients), seeing each one's role, scope, accounts and last sign-in, and
  confirms it, says what changed, or removes access. A completed review is recorded in the action
  log with a digest of every decision. A customer that manages its own people reviews them the same
  way. Checked each quarter for the bank and for each self-managing customer.
- **Files reconciled**: an X9.37 or NACHA presented-items file keeps the items and total its control
  records declare, and what was loaded; the Controls page warns when rows refused on the way in
  weren't corrected within a day.
- **Decision files acknowledged** by the core within a day; **outside services** (email, SMS, OCR,
  storage, SIEM, identity providers) reviewed each year (Settings → Outside services); and **your
  standards** (Settings → Your standards): the password policy and the signing keys' age checked
  against the minimum length and rotation period you record. 45 checks in all.

CUEC mappings and attestations (CONTROLS_PLAN.md CT4):

- **Mappings** (Settings → CUECs): map each CUEC to your own control (its id, name, owner and a link
  to it in your GRC tool), one at a time or by loading the `cuecs.csv` you filled in.
- **Attestations**: at the end of each period (monthly, quarterly or yearly) OpenClear proposes one
  for each automated CUEC from its checks' results: *operated* when the check ran at least 95% of
  its scheduled times, no deviation outlasted your grace period and every population test passed;
  *operated with exceptions* or *not tested* otherwise, always with the numbers. You accept it (or a
  mapping accepts automatically, which is a security event to turn on) or reject it with a reason.
  Assisted and attested CUECs are attested by a person, with files attached.
- **Signed**: an accepted attestation is canonical JSON signed with the installation's key (ECDSA
  P-256), downloadable with the public key; it never changes, and a correction supersedes it. You
  can require a second person to accept what someone prepared.
- **Customers** attest their own CUECs under *Your responsibilities*. New permissions
  `compliance:read` and `compliance:attest` (the Admin group's by default); webhooks
  `compliance.attestation.prepared` and `.accepted`.
- **The host's CUECs**: attested by each bank by default, or once for a shared installation by its
  platform staff (`OPENCLEAR_HOST_CUECS_ATTESTED_BY=platform`), which every bank sees read-only.

## 1.17.0 — 2026-10-16

Controls (CONTROLS_PLAN.md CT1):

- **OpenClear's control inventory**: 41 controls (`OC-AC-05`, "Segregation of duties"), each
  filed under the NIST SP 800-53 Rev. 5 controls it carries out, with its type, whether a
  bank's setting governs it, the checks that test it and the CUECs it relies on. On the docs
  site (Compliance → OpenClear's controls).
- **The Controls page by control**: the checks are grouped under the controls they test, by
  family, each control with its id, NIST controls and status (the worst of its checks).
- **API**: `GET /api/v1/compliance/controls`, the inventory with each check's live status for
  the bank, and `GET /api/v1/compliance/cuecs`, the CUEC inventory with the controls that rely
  on each (a customer's own session sees only the customer's). The SDKs are regenerated.

Customers' own API clients (MOBILE_PLAN.md R4):

- **A third self-service switch**: on a customer's page, the bank can let the customer's own
  administrators make API clients for its own systems (`customer_api_client:manage`), and
  `PUT /api/v1/customers/{number}/self-service` takes `manages_api_clients` (left out, it
  stays as it is).
- **Settings → API clients for a customer**: name, a role the bank offers, all or some of its
  accounts, read only or not; the secret shown once; rotate, switch to a key pair, revoke.
  Never acting for users or linked to a sign-on connection. Recorded against the customer
  (its activity page) and as the usual `api_client.*` security events.
- **The bank sees them**: on its API clients page, marked *theirs*, where it can revoke them;
  `GET /api/v1/api-clients` shows `managed_by` and `accounts`, and a customer's own session
  sees only the clients it made. The Customer self-service control counts them.

Plugin page additions (MOBILE_PLAN.md R5; PLUGIN_PLAN.md PL4):

- **UI slots**: a plugin can add a panel (exception, customer, dashboard), a toolbar button
  (exceptions, issued items), a row action (exceptions, paid items), a page in the menu, or a
  control-panel tile. Each bank turns each one on under Settings → Plugins ("Page addition").
- **Safe by design**: a slot's HTML is sanitised (no scripts, styles or event handlers, so the
  Content Security Policy is unchanged); it runs with its own database session limited to the
  person's bank, customer and accounts; one that fails, takes over 200 ms or returns something
  unsafe is skipped and logged (`plugin.slot.failed`), and the page renders without it.
- **A plugin's own routes and files**, at `/ui/plugins/<plugin>/` (answering only for banks that
  turned on one of its slots) and `/static/plugins/<plugin>/`, with dependencies for the person,
  permissions, CSRF and the action log in `openclear.plugins.api`.
- **On a phone** a toolbar folds behind one "More" button and a panel starts closed.
- The example plugin fills one slot of each kind (docs/PLUGINS.md).

Bank approval of new customer users (MOBILE_PLAN.md R6), off by default:

- **Settings → New customer users**, and each customer's own setting on its page (follow the
  bank, approve, don't): people a customer's side adds (an invitation, its own sign-on or its
  SCIM directory) wait for the bank before they can sign in, and are told so when they try.
- **New customer users** in the menu when anyone is waiting: approve, or reject with a reason
  (their access to that customer is removed). The customer's administrators are told either way
  (a new notification), and both are in the action log as `user.approved` and `user.rejected`
  security events.
- Only the bank's approval lets a waiting person in: signing in again through the customer's
  sign-on, its SCIM directory and the customer's administrators can't. People the bank adds
  itself never wait.

## 1.16.0 — 2026-10-16

Phones and tablets (MOBILE_PLAN.md R0–R3). The desktop (1024px and wider, with a mouse) is unchanged.

- **Navigation drawer**: below 1024px the sidebar is a bar with the bank's name and a menu
  button; the links slide in from the left (Escape, the close button or a tap outside closes
  it).
- **Lists as cards on a phone**: issued items, paid items, stop payments, ACH transactions and
  authorizations, accounts, customers, the action log, Users, People and API clients. Each row
  is a card (the item as its title, the amount beside it); the column headers stay above the
  cards as sort buttons. Users' and API clients' actions open from a ⋯ button.
- **More filters** on a list opens as a sheet from the bottom of a phone's screen.
- **Touch targets**: below 1024px every button, link in a list, field and menu entry is at
  least 44×44px, and the theme and Help controls no longer cover the first card.
- **Deciding on a phone or tablet**:
  - The exception queue is cards on a phone, a checkbox down each card's left edge;
    Network and Status open as a sheet, and so do the reasons for returning several at once.
  - On an exception, Pay and Return sit in a compact bar at the bottom of the screen with the
    fraud risk beside them; Return's reasons open as a sheet (R still opens them).
  - On a phone the check image comes first, front or back at a time.
  - **Undo**: recommending a decision (one tap on Pay) shows a message with Undo, which takes
    the recommendation back while nobody has approved it yet. The exception's page offers the
    same as *Withdraw my recommendation*. A decision itself is never undone.
  - A tablet held sideways shows the queue and the chosen item side by side; deciding there
    keeps the queue in view, and Back works.
- **Action log on an exception**: the exception's own entries, collapsed with the newest one
  showing, for people who can read the action log (not a customer's own people).
- **Fixed**: the bar for deciding several exceptions at once now stays at the bottom of the
  screen while scrolling, as intended.
- **Fixed**: lists sorted by when rows were made (issued items, paid items, ACH transactions,
  exceptions, Users) could swap rows made in the same second between pages; every sort now
  ends with a tie-breaker.
- **The rest of the bank's pages**: security groups, check and ACH return reasons, a
  customer's accounts, Controls, the integration checklist, plugins, ML models and
  segregation of duties read as cards on a phone; no table is cut off on a phone or tablet.
  An upload form's column guide is one column per card. On an exception, what was expected
  and what was presented fit a phone, each mismatch marked beside its label.
- **Dashboard**: its figures are two to a row on a phone, so all of them fit on one screen.
- **Tablets**: Users' and API clients' row actions open from the ⋯ button below 1024px, not
  only on a phone; a touch screen 1024px or wider (a landscape iPad) gets 44px targets too.
- **Fixed**: Retire on the check return reasons list was cut off on a phone.
- **Fixed**: the notification preferences' checkboxes had no labels for screen readers.
- **Desk work stays on the desk**: on a phone, the lists' Bulk upload and Export buttons, the
  bank's Users and the customer's People exports, Billing's reports and exports, an upload's
  downloads and Controls' evidence export aren't shown. Tablets and desktops keep them.
- **No image for this check**: an exception on a check with no image says so under what was
  expected and what was presented, with *Upload an image* for people who can (it was only
  in the collapsed *More about this exception*).

Controls (CONTROLS_PLAN.md CT0):

- **Complementary user entity controls**: what OpenClear's controls rely on the bank, its
  business customers and whoever hosts it to do, 57 in all, each filed under its NIST SP
  800-53 Rev. 5 control (`B-AC-5-01`, `C-IA-2-01`, `H-SC-28(1)-01`). For each: what OpenClear
  does that relies on it, how OpenClear can evidence it, and where. On the docs site
  (Compliance → User entity controls), and as `docs/compliance/cuecs.json` and `cuecs.csv`
  to map onto your own control inventory.

## 1.15.0 — 2026-10-13

Customers can manage their own people and sign-on, when their bank allows it
(CUSTOMER_ADMIN_PLAN.md).

- **The bank's switches**: on a customer's page, *Self-service* lets its own administrators
  manage its people, its sign-on, or both (also `PUT /api/v1/customers/{number}/self-service`).
  Security groups get *Offered to customers*: the only roles a customer can give. A new
  control, *Customer self-service*, lists who manages themselves and warns when one has no
  administrator. The customer's page links to its people and sign-on activity.
- **People** (Settings → People, `customer_user:manage`): invite by email or from a CSV,
  choosing role, accounts and passkey requirement; change, deactivate, reactivate; reset a
  password, unlock or sign out everywhere for people who belong to that customer alone; export;
  an activity log. Invitations create nothing until accepted: a new person chooses a password,
  someone who already uses OpenClear confirms with theirs (a wrong one counts toward their
  lockout). The last administrator can't be removed.
- **Sign-on** (Settings → Sign-on, `customer_sso:manage`): the customer's own OIDC or SAML
  connection, mapped only to offered roles; email domains verified by DNS TXT record, outside
  which neither sign-in nor SCIM creates anyone; SCIM tokens; *Test sign-in*; password sign-in
  turned off only from a session signed in through sign-on, and never leaving the customer
  with no way in. Customers can also tighten their own password rules.
- **Sessions from a customer's sign-on stay in that customer**: switching organization needs
  a fresh sign-in there.
- **Fixed**: under Postgres row-level security, the checks that stop a bank resetting the
  password of (or SCIM changing the email of) someone who also belongs to another bank saw
  only the bank's own memberships, so they let it through. They now count every bank's,
  through a narrow policy addition for one named person.
- **Migration** `c6e8a0c2e4f7`: `user_invitation`, `customer_email_domain`;
  `customer.self_manages_users`/`self_manages_sso`, `security_group.offered_to_customers`,
  `sso_connection.managed_by`; `notification.recipient_user_id` nullable; the
  tenant_membership policy's probe.

## 1.14.0 — 2026-10-13

What a bank's security and IT reviews ask for next: controls that alert, access by account,
API clients without shared secrets, and Azure storage.

- **A control changing status is a security event**: `control.status.changed`, when any
  control (Settings → Controls) turns WARN or FAIL, and when it recovers, with the old and
  new status. Recorded whenever the controls are read and checked hourly, so a SIEM can
  alert on a FAIL nobody was looking at (docs/guides/siem.md).
- **Per-account access**: the bank can limit one of a customer's people (or a customer's API
  client) to some of the customer's accounts (Users → Edit → Choose accounts, or
  `PUT /api/v1/users/{membership_id}/accounts`). They then see, change and are told about
  only those accounts' items, on every page and endpoint, and their bulk uploads and data
  exports cover only those accounts. The access review (`GET /api/v1/users`) shows it.
- **API clients can sign in with a key pair** (`private_key_jwt`, RFC 7523): register the
  client's public key, and it signs a short assertion with a private key that never leaves
  its systems; each assertion is accepted once. Its secret stops working (API.md). The
  "Integration secrets rotated" control no longer counts key-pair clients.
- **Azure Blob Storage** for files: `OPENCLEAR_STORAGE_BACKEND=azure`, with a connection
  string or a managed identity (`pip install 'openclear[azure]'`). Tested against Azurite.
  `scripts/move_files_to_object_storage.py` moves existing files there too.
- **Migration** `b5d7f9b1d3e6`: `control_status`, `client_assertion_use` and
  `membership_account`; `api_client.token_auth_method` and `public_key_pem`;
  `tenant_membership.all_accounts`.

## 1.13.0 — 2026-10-12

Reporting through each bank's own business intelligence platform: a guide, and what a data
pipeline needs from the API. It brings:

- `docs/guides/bi.md`: connecting Power BI, Tableau, Looker, Qlik or Superset through a warehouse;
- `updated_since` on every list, with cursor paging that never misses a row;
- read-only API clients, including a customer's own data feed;
- the full data export through the API;
- for the sandbox, the caller's real address behind Fly's proxy.

### For banks' data pipelines and BI platforms (docs/guides/bi.md)

- **`updated_since` on every list** (exceptions, issued and presented checks, stop payments,
  ACH filters and transactions, accounts, customers): only rows changed since a time, in the
  order they changed. Pages follow a `Link: rel="next"` cursor (`updated_since` with
  `after_id`), so a row that changes mid-way is never skipped. Each row now carries
  `updated_at` and `customer_id`.
- **Read-only API clients**: tick "Read only" when creating a client; whatever its group allows,
  it can only read (and run the full data export). Limited to one customer, it's that
  customer's own data feed.
- **The full data export through the API**: `POST /api/v1/data-exports` (the bank, or one
  customer with `customer_number`; a customer's client always its own), poll
  `GET /api/v1/data-exports/{id}`, download `file_url`.
- The BI guide covers all three.
- **Migration** `a4c6e8a0c2d5`: `updated_at` on exceptions, presented checks, ACH transactions
  and filters, stop payments, accounts and customers (from their creation time), indexes for
  changed-since reads, and `api_client.read_only`.

## 1.12.0 — 2026-10-12

Tools for the bank's staff and for whoever runs the installation, and for putting OpenClear
behind an edge network. It brings:

- the customer hub: customers with items to decide first, and "send a reminder now";
- platform staff, the operator view across banks, and setting up a bank from the web;
- support access granted by a bank, instead of impersonation;
- the edge check and the edge deployment guide for Cloudflare, Akamai and others.

### Bank staff and operator (MASTER_PLAN.md M6)

- **The customer hub.** The Customers list puts customers with items to decide first, with
  open items, items due today, the next deadline, the last issued-check file and the decision
  default at the deadline; search, and "No issue file in N days". A customer's page shows its
  open items and has **Send a reminder now**. The exceptions list filters by customer for bank
  staff (`?customer=`).
- **Platform staff.** People who run the installation (`scripts/platform_staff.py`, operator
  or support) sign in at `/ui/platform/login`, separately from any bank.
- **The operator view** (`/ui/platform`): every bank's customers, open and overdue
  exceptions, jobs, last import, controls and security-event delivery; setting up a bank from
  the web, with a first admin who sets their own password.
- **Support access instead of impersonation.** Settings → Support access grants a named
  support person access for a reason and a period (up to 72 hours), read-only unless a second
  admin approves write access, never to decide items or see secrets, users, groups, settings
  or the action log. They work under their own name, with a banner; ending the grant or its
  time running out ends the session at once. Every grant, approval, use and end is in the
  action log, as a security event, and in the evidence export.
- **Accessibility**: a table that scrolls sideways is now reachable by keyboard (focusable,
  and named, only while it overflows).
- **Migration** `f3b5d7f9b1c4`: `support_access_grant`, and the platform role on users.

### Edges (MASTER_PLAN.md M7)

- **The edge check**, `scripts/edge_check.py <url> [--origin <origin>] [--expect-ip <ip>]`:
  through Cloudflare, Akamai or a load balancer, it checks that OpenClear sees the caller's
  real address, the origin refuses requests that go around the edge, security headers arrive
  unaltered and nothing is injected, pages and the API aren't cached while static files are,
  responses are compressed, and legitimate requests (X9.37 and NACHA uploads, a large SAML
  response, SCIM, markdown with HTML-like text, URLs in settings, a burst of API calls) get
  through the WAF and bot management. It needs no account and changes nothing; `--json` for
  a report; exits 1 on a failure.
- **`GET /api/v1/edge/echo`**: how OpenClear saw the request (the caller's address and where
  it came from, whether the edge was trusted, the edge headers, the request id). No sign-in.
- **The edge deployment guide**, `docs/guides/edge.md`: origin lockdown, settings, WAF
  exceptions path by path, bot management, features to turn off, caching, sizes and timeouts,
  error pages, a go-live checklist, and notes for CloudFront, Azure Front Door and F5.
- `scripts/edge_ip_ranges.py cloudflare` prints Cloudflare's ranges for
  `OPENCLEAR_TRUSTED_PROXY_CIDRS`.
- Tested against a stub edge in the suite (`tests/stub_edge.py`) that behaves like Cloudflare
  and Akamai, including the mistakes real setups make; a real zone confirms it later.

## 1.11.0 — 2026-10-12

Settings you can see at a glance, reminders before the cutoff, security events delivered to
a bank's SIEM, and uploads in a customer's own format. It brings:

- one control panel for Settings and Admin, at bank and customer level;
- email and text reminders before each cutoff, with bank timing and a customer override;
- security events delivered to syslog, Splunk HEC or HTTPS, checked against a real Splunk
  and syslog-ng, with a control that fails when delivery falls behind;
- issue-file layouts built from a sample file, and failed rows fixed in the browser;
- drag and drop on every upload page, and fixes for pages that scrolled sideways on phones.

### Control panel, reminders and event shipping (MASTER_PLAN.md M4)

- **Settings is one control panel.** Settings and Admin are now one page of tiles, in five
  sections, each tile showing how its setting stands ("Dual control · On", "Controls · 1
  WARN"), with a search box. A person sees only the tiles their permissions allow.
  - Small settings open in a dialog with its own URL (`/ui/settings/branding`), so it can
    be bookmarked or linked; without JavaScript it's a plain page. Back closes it, focus
    returns to the tile, closing with unsaved changes asks first, saving updates the tile
    in place, and errors show in the dialog. It's full-screen on phones.
  - Lists and workflows (users, SSO, webhooks, controls…) open as pages with "Back to
    settings", which returns to their tile with the search kept.
  - A business customer's own users get their customer's panel: reminders, banner and
    billing statement.
  - `/ui/admin` redirects to the panel; the ML models page is now `/ui/admin/ml-models`.
- **Reminders before the cutoff.** Everyone who can recommend or decide an exception gets one
  message per reminder, "N items to decide, due in X", by email or text per their
  notification preferences. Timing per bank (Settings → Reminders; 2 hours and 30 minutes
  before by default, `OPENCLEAR_REMINDER_OFFSETS_MINUTES`), with a customer override.
- **Check return reasons** have their own settings page: add, edit, retire and restore,
  with their X9.100-188 codes. `ach_return_reason:manage` now covers both lists.
- **Security events delivered to a SIEM.** Syslog (RFC 5424 over TLS, as JSON, CEF or LEEF),
  Splunk HTTP Event Collector (with indexer acknowledgement) and HTTPS JSON. Each bank adds
  its own destination in Settings → Security event forwarding, with a test event; the
  operator can set one for the whole installation (`OPENCLEAR_SIEM_*`). Delivery is in
  order and at least once from a cursor, with backoff and catch-up, and never slows a
  request (`OPENCLEAR_ENABLE_SECURITY_EVENT_DELIVERY_SCHEDULER`). Events a destination
  hasn't received are kept past their retention until it has.
  TLS syslog is octet-counted (RFC 5425), plain TCP one message per line. Checked against
  real Splunk (HEC with acknowledgement, and its TCP syslog input) and a syslog-ng relay.
- **A new control, "Security events delivered"**: it fails when a destination has been
  failing for an hour or falls behind, and warns when there's no destination and nobody has
  read the pull API for a day. The evidence export adds the period's event counts by
  category and each destination's delivery record.
- **New security events**: `webhook.changed`, `users.exported`, `secrets.reencrypted` and
  `security_events.destination.changed`.
- **Docs**: a SIEM guide (`docs/guides/siem.md`) for Splunk, Sentinel, QRadar, Elastic and a
  syslog relay, with suggested detections; a log-management row in the FFIEC crosswalk.
- **Fixes**:
  - Sortable table headers are now real buttons inside the column header (axe flagged
    `aria-sort` on `role="button"`).
  - The scheduler now starts when only the decision outbox or control tests are turned on.
- **Migration** `d1f3b5d7f9a2`: reminder timing on banks and customers,
  `deadline_reminder`, and `security_event_destination`.

### Uploads (MASTER_PLAN.md M5)

- **Issue-file layouts.** A customer's issued-check files in their own format: delimited (any
  delimiter, with or without a header, columns by name or position), fixed-width (start and
  length per field, a record prefix, header and trailer lines skipped) or Excel. Amounts
  with a decimal point, implied cents or a separate cents column; named date formats; a void
  flag (a void line voids the check already issued); leading zeros dropped if asked.
  - Built from a sample in Settings → Issue-file layouts: choose what each column holds above
    the sample's first rows, or select a fixed-width field's characters; preview what would
    be imported, errors included, before saving. A short library of common shapes to start
    from. Bank-wide or per customer, with a default.
  - Used by the upload page, the API (`format=layout`, `layout_id`;
    `GET /api/v1/bulk-uploads/layouts`) and the drop folder (the default). Each upload keeps
    a copy of the layout it used.
  - A file whose columns don't match offers "Save these settings as a layout", starting from it.
- **Fixing failed rows in the browser.** Each failed row names the field at fault. "Fix
  failed rows" shows them editable, with the error beside the field; only the ticked rows are
  imported, each recorded as a correction with who made it, in the action log, and reversed
  by backing the upload out. The uploaded file never changes.
- **Drag and drop** onto every upload page's file input.
- **Fixes**: a wide table inside a form, or a visually hidden label in a table header, no
  longer widens the page on a phone.
- **Migration** `e2a4c6e8a0b3`: `import_layout`, `bulk_upload_correction`, and the failed
  field on row results.

## 1.10.0 — 2026-10-11

A release for running OpenClear in production, and for deciding faster. It brings:

- the real caller's address and per-person rate limits behind Cloudflare, Akamai or a load
  balancer;
- security events (sign-ins, failures, lockouts and more) with pull feeds for a bank's SIEM,
  and optional JSON logs;
- lists that search and export across every row, search everywhere, and clearer navigation;
- Pay and Return buttons, check return reasons, deciding several items at once, and "decide
  now" for a customer's people;
- branded pages for every error, and 503s that load balancers understand;
- long replays and evidence exports as background jobs.

### Request context and security events (MASTER_PLAN.md M1)

- **The caller's address behind an edge.**
  - `OPENCLEAR_CLIENT_IP_HEADER` and `OPENCLEAR_TRUSTED_PROXY_CIDRS` read Cloudflare's or
    Akamai's client-IP header, trusted only from the edge's ranges.
  - The address is worked out once per request, and used by rate limits, the action log,
    logs and security events.
- **Rate limits per person.** Signed-in people and API clients are limited as themselves
  (600 a minute by default); others by address.
- **Origin lockdown.** An optional secret header from the edge (`OPENCLEAR_EDGE_SECRET`),
  or a recorded attestation. There's a new control, "Edge network set up".
- **Request ids.**
  - `X-Request-ID` on every response: the edge's own id when trusted.
  - It's shown on error pages and recorded in logs and security events.
  - The action log records it, with the caller's address, in each entry's signed metadata.
- **JSON logs** (`OPENCLEAR_LOG_FORMAT=json`), with the request's context and an access line
  per request. Outbound HTTP URLs are no longer logged.
- **Security events.**
  - Recorded: sign-ins, failed sign-ins (with the reason), lockouts, second factors,
    tokens issued and refused, SSO failures, act-as and permission refusals, rate-limit
    refusals (sampled), and security-relevant changes from the action log.
  - Pull feeds: `GET /api/v1/security-events`, `GET /api/v1/audit-log` (with signatures),
    and `GET /api/v1/platform/security-events`.
  - The CLI follows them with `openclear events tail`.
- **Serving.**
  - A configurable keep-alive in the Docker image (`OPENCLEAR_KEEPALIVE_SECONDS`, 75).
  - Year-long caching for versioned static files.
  - A JSON `413` under `/api`.
  - The sandbox image no longer trusts forwarded headers from any address.

### Lists, search and shared components (M2: UX wave 0)

- **The main lists search the server, across every row.**
  - Issued items, paid items, ACH transactions and exceptions. Until now, the filter box
    searched only the rows on the page, so an item on another page looked as if it wasn't
    there.
  - Search by check or trace number, payee or originator, amount (exact or a range), date
    range and account number.
  - Sort any column across every row.
  - Export what's listed as CSV. Each export is in the action log, and is a security event.
  - Other paginated tables now say "Filter this page only".
- **Search everywhere** from the navigation: checks, ACH entries and exceptions, among what
  the person may see.
- **Navigation by work:**
  - Exceptions first, with a count of those needing attention.
  - "Action Log" throughout, where both "Audit Log" and "action log" were used.
- **Detail pages:**
  - Page titles name the item (e.g. "Check 1234 · $5,000.00 · Exception").
  - "Back" returns to the list you came from, with its filters.
- **Empty lists** say what to do next, with the button for it.
- **Shared components:**
  - a confirmation dialog in place of the browser's `confirm()`;
  - toasts for "saved";
  - submit buttons that can't be pressed twice while a form is being sent.
- **An accessibility check in the test suite.** axe-core in a browser checks ten main pages
  against WCAG 2.1 AA; the two problems it found (pager contrast) are fixed.

### Long requests as background jobs (M2: WAF wave B)

- **A replay** of a file over 1 MB, and **an evidence export** of more than 32 days, run in
  the background: `202` with a run to poll, then a download. Smaller ones answer at once as
  before. `openclear audit export` waits for the job.
- **Verifying the action log** runs in the background, and daily with the control tests.
  The Controls page shows the latest result and its age, instead of walking the whole log
  on every view.

### Deciding (M3: UX wave 1)

- **Pay and Return are two buttons.**
  - Paying needs no reason, and can take an optional note.
  - Returning asks why, from the bank's list for that kind of item.
  - Checks now have a list too: altered or fictitious, not authorized, stop payment,
    duplicate presentment, refer to maker, stale dated, signature irregular. Each has its
    X9.100-188 code, which goes with the decision to the core.
  - Paying a high-risk item still asks first.
- **Several at once.** Tick items on the exceptions list, then Pay or Return them together,
  with a confirmation of how many and how much. Each is decided, or recommended under dual
  control, exactly as from its own page; anything that can't be done is listed.
- **Decide now.** A customer's people with checks waiting land on them after signing in,
  and every page shows how many are waiting and the soonest deadline. It's amber within two
  hours and red within 30 minutes. The navigation's Dashboard link still opens the
  dashboard.
- **The exception page** leads with what differs, the decision and the check image. The
  rest is under "More about this exception". Keys: P to pay, R to return; J and K move
  through the list.
- **On phones** the list is cards, and the decision buttons are full width and stay in
  reach. The Pay and Return colours pass WCAG AA contrast.
- **Fixed:** "decide by" times read from SQLite showed hours out in the browser. They lacked
  a time zone, so the browser took them for local time.

### Error pages

- **Branded pages for every error on a web page.** Each has a message that fits it, a way
  back and a reference to quote to support. They replace raw JSON or plain text for:
  - an unknown address (404, with search);
  - a method a page doesn't take (405);
  - an expired form (403, "This form expired… reload the page");
  - a broken address or form (422);
  - a file over the size limit (413, stating the limit);
  - too many requests (429, with `Retry-After`).
- **The API answers JSON for all of them.** 413 and 429 were plain text.
- **A database that can't be reached is a 503 with `Retry-After`**, not a 500. `/health`
  checks the database and answers 503 when it doesn't answer.
- **Self-contained 502, 503 and 504 pages** (`static/errors/`) for a load balancer or edge
  to show when OpenClear can't answer at all.

### Upgrading from 1.9.0

- **Run `alembic upgrade head`.** It adds the `security_event` and `check_return_reason` tables, and gives every
  existing bank the default check return reasons.
- Behind Cloudflare or Akamai, set the client-IP header and ranges (README, "Reverse proxy /
  WAF deployment"). Until then, the new control fails, and everyone shares one rate-limit
  bucket per edge address, as before.

## 1.9.0 — 2026-10-09

A compliance release: evidence that OpenClear's controls work, for a bank's compliance
staff, auditors and examiners. It brings:

- segregation of duties, with two-person changes to rules and models;
- model risk tests: replaying past files through today's rules, and synthetic fraud
  capture;
- continuous control monitoring (Admin → Controls);
- a signed evidence export;
- AES-256-GCM for secrets stored in the database;
- crosswalks to FFIEC, Nacha, SR 11-7 and third-party risk guidance.

### Segregation of duties (COMPLIANCE_PLAN.md C1)

- **Conflicting duties.** Two rules apply to every bank:
  - whoever submits items (paid items, issued items, ACH, check images) can't decide exceptions;
  - whoever manages users can't also define what security groups grant.
- **Per transaction.** The person who presented an item can't decide its exception, even after
  moving to another group.
- **Warn or enforce**, chosen in Settings → Segregation of duties.
  - Every bank starts in **warn**: breaches are allowed and each one is logged as `sod.violation`.
  - **Enforce** refuses a breach with `403` and `ERR_SOD_VIOLATION`, and still logs it. It can only
    be turned on once no security group combines conflicting duties. The default Admin and
    Bookkeeper groups do, so they need splitting first.
- **Two-person changes** under enforce: turning a matching rule on or off, and activating an ML
  model, wait in Admin → Changes for a second admin. Nobody can approve their own change.

  The API's model activation follows the same rule: under enforce it answers `202` with a
  pending change.

### Model risk management (C2)

- **Replay** (Admin → Model risk, `POST /api/v1/mrm/replay`) runs a past X9.37 or NACHA file
  through today's rules in a dry run. It reports how they compare with what actually happened
  to each item: agreement, false positives, missed returns, unhandled items, and the items
  that differ.
- **Anomaly capture** (`POST /api/v1/mrm/anomaly-capture`) checks that the live rules catch
  synthetic fraud and refuse malformed files: a duplicate check, an altered amount, the wrong
  account, an ACH debit over its limit or from an unknown originator. It can run daily
  (`OPENCLEAR_ENABLE_CONTROL_TEST_SCHEDULER`).
- Both are dry runs: nothing they create is kept except the report.

### Evidence export (C3)

- `GET /api/v1/audit/export?start&end&format=json|pdf`, Admin → Controls → Export evidence,
  and `openclear audit export` give a signed evidence bundle for a period. It covers: the
  system version; rules, plugins and models with their digests; security groups; whether the
  action log verifies; segregation-of-duties breaches; changes and approvals; every decision
  with who made it; model-risk runs; and the control status.

### Continuous control monitoring (C4)

- **Admin → Controls**, `GET /api/v1/controls/status` and `openclear controls` show each
  control's live status, PASS / WARN / FAIL, with evidence.
- **Secrets in the database are now AES-256-GCM** (they were Fernet, AES-128), with key ids.
  Existing values are re-encrypted at startup. Rotate the key by moving the old value to
  `OPENCLEAR_SSO_ENCRYPTION_KEY_PREVIOUS` and setting a new `OPENCLEAR_SSO_ENCRYPTION_KEY`.
- `OPENCLEAR_STORAGE_ENCRYPTION_ATTESTATION` records how the host encrypts disks and the
  database, which OpenClear can't see itself.

### Compliance crosswalks (C5)

- `docs/compliance/`: FFIEC IT Handbook, Nacha Operating Rules, SR 11-7 model risk, and
  2023 interagency third-party risk guidance, with a due-diligence pack. They're also on
  the docs site.

### Upgrading from 1.8.0

- **Run `alembic upgrade head`.** It adds `tenant.sod_mode`, and the `change_request` and
  `control_run` tables.
- Stored secrets move to AES-256-GCM on the first start. To keep a rollback to 1.8.0
  possible, back up the database first: 1.8.0 can't read the new format.

## 1.8.0 — 2026-10-09

An integration release: cores, online banking providers and identity systems can now
connect to OpenClear the way they expect, and others can extend it without changing it.
It brings:

- SDKs for Python, TypeScript and .NET;
- an API for a bank's systems (API clients, decision feed, webhooks, idempotency);
- SAML and SCIM;
- embedding in online banking;
- decision and NACHA return files;
- plugins;
- a developer sandbox with an integration checklist.

### Upgrading from 1.7.0

- **Run `alembic upgrade head`.** The Docker image does this on startup. It adds new tables
  for:
  - API clients;
  - idempotency keys;
  - decision feed cursors;
  - webhooks;
  - plugin choices;
  - SSO identities and SAML assertions;
  - SCIM;
  - embed launches;
  - the decision outbox;
  - conformance results.

  It also adds new columns on `sso_connection`, `tenant`, `api_client`, `decision` and
  `exception_item`, and existing decisions are numbered for the decision feed. Every new
  bank table is under row-level security on Postgres.
- **New dependency:** `python3-saml`. Its `xmlsec` dependency has prebuilt wheels, so no
  system packages are needed. Optional: `pip install 'openclear[docs]'` for the docs site.
- **Behaviour changes:**
  - Existing Admin groups get `api_client:manage`.
  - The default ACH return reasons get their NACHA codes where none was set.
  - An identity provider no longer attaches a new membership to an existing person who
    isn't already a member in that scope; add them as an admin first (Security fixes,
    below).
  - In the API contract, file downloads are declared as files.
- **Nothing new runs until you turn it on:**
  - plugins (`OPENCLEAR_PLUGINS`);
  - the decision outbox (`OPENCLEAR_ENABLE_DECISION_OUTBOX_SCHEDULER`);
  - sandbox sign-up (`OPENCLEAR_SANDBOX_SIGNUP_ENABLED`; never on an installation with
    real banks);
  - embedding (Settings → Embedding);
  - SAML and SCIM (per connection).
- **Downgrading to 1.7.0** is supported: `alembic downgrade b9d1f3a5c7e8`. Back up first.
  The new tables and columns are dropped, along with the data in them (API clients,
  webhooks, SSO identities, …). The default return reasons keep the codes they were given.

### Security fixes (review of this release's new features; SECURITY_REVIEW.md, 2026-10-09)

- **Account takeover by an identity provider:** sign-in, token exchange and SCIM no longer
  attach an identity provider's asserted email to an existing person who isn't already a
  member in that connection's scope. An admin adds such a person first. New people are
  provisioned as before.
- **Open redirect:** `next` paths with backslashes or control characters (`/\evil.example`)
  are refused.
- **Embedding origins:** these can no longer inject into the Content-Security-Policy
  header.
- **Sandbox sign-up:** it refuses to run on an installation with real banks.

### New: developer sandbox and integration checklist (SDK_PLAN.md P4)

- **Developer sandboxes:** on an installation run as a sandbox, a vendor gets a free,
  seeded sandbox bank at **/ui/sandbox**, with its admin sign-in and an API client. It's
  wiped after 30 days. The setting is `OPENCLEAR_SANDBOX_SIGNUP_ENABLED`; never turn it on
  where real banks are.
- **Integration checklist** (Admin → Integration checklist, `GET /api/v1/conformance`):
  shows what a core or online banking integration has done. A **webhook probe** checks that
  its receiver rejects a forged signature. See
  [docs/guides/certification.md](docs/guides/certification.md).
- **Test files:** `scripts/sandbox_test_files.py` builds issued-check, X9.37 and NACHA test
  files with Moov, from CheckSim's synthetic checks. `expected.csv` says what each item
  should become.
- **`sandbox/`:** the sandbox stack. It includes Apache Fineract and the Fineract adapter,
  which was checked against a real Fineract.
- **Docs site:** `python scripts/build_docs_site.py` builds a developer docs site from the
  repo's docs.
- **Docker image:** it now keeps decision files and audit archives under `/data`.
- **Upgrading:** run `alembic upgrade head`. It adds the tenant columns `is_sandbox`,
  `sandbox_expires_at` and `sandbox_contact_email`, and the table `conformance_result`.

### New: the `exception.deadline_approaching` webhook

- **What it does:** sent once per open exception when its decision deadline is near. The
  default is 4 hours before; change it with `OPENCLEAR_WEBHOOK_DEADLINE_WARNING_HOURS`.
- **When it's checked:** by the disposition sweep.
- **Upgrading:** run `alembic upgrade head`. It adds the column
  `exception_item.deadline_warned_at`.

### New: decision files for cores (SDK_PLAN.md P3)

- **Decision outbox:** turn it on under **Settings → Decision files**. OpenClear writes each
  run's new decisions to a file on the bank's schedule and sends the `decision_batch.ready`
  webhook.
- **Getting the files:** a core downloads and acknowledges each one at
  `/api/v1/decision-batches`.
- **NACHA return files:** the new `nacha_returns` layout writes the ACH debits the bank
  returned as a NACHA return file for its ACH operator. Set the bank's routing number and
  the operator's under Settings → Decision files. The files pass Moov's ACH validator.
- **Return reason codes:** the default ACH return reasons now carry their NACHA codes
  (R01, R02, R03, R08, R09, R10, R11). An existing bank's defaults get them too, unless
  the bank set its own.
- **For plugin authors:** decision layouts take the bank's settings:
  `write(feed, settings)`, with `config_fields`.
- **Scheduling:** set `OPENCLEAR_ENABLE_DECISION_OUTBOX_SCHEDULER=true`, or call
  `workers.tasks.decision_outbox_job` from cron.
- **Upgrading:** run `alembic upgrade head`. It adds the tables `decision_outbox` and
  `decision_batch`, and fills in return codes.
- **The `openclear` command** (in the Python SDK): `push` a file, `decisions pull`,
  `decisions files`, and `webhooks tail`, for scripts and cron.
- **An Apache Fineract adapter** (`integrations/fineract`). It:
  - syncs customers and accounts;
  - sends checks paid from savings accounts as presented items;
  - undoes a returned check's withdrawal in Fineract.
  It's a reference for other cores too.
- **A .NET SDK** (`sdks/dotnet`, `OpenClear.Sdk`). It's generated from the same contract
  and has the same retries, paging, errors and webhook verification as the Python and
  TypeScript SDKs.

### New: OpenClear inside online banking (SDK_PLAN.md P2)

- **Embedding:** a bank's business customers can review and decide exceptions inside the
  bank's online banking, without signing in again.
- **How a session starts:** the online banking server, acting for the signed-in person,
  asks `POST /api/v1/embed/launch` for a one-time link (good for a minute). Its page
  shows the link with the new **`@openclear/embed`** library (`sdks/embed`), or in a
  WebView in a mobile app.
- **What the embedded pages are like:**
  - no navigation;
  - framed only by the origins the bank lists under **Settings → Embedding**;
  - they tell the host page when they resize, navigate, record a decision or end.
- **Ordinary sessions** still can't be framed at all.
- **Guide:** [docs/guides/embedding.md](docs/guides/embedding.md). There's also a runnable
  host page in `examples/embed-host`.
- **Upgrading:** run `alembic upgrade head`. It adds the column
  `tenant.embed_allowed_origins` and the table `embed_launch`.

### New: online banking sign-on profile and token exchange (SDK_PLAN.md phase F)

- **A connection for an online banking provider:** the provider is the identity provider,
  and a **customer claim** names each person's business customer. Each session is
  limited to that customer.
- **Token exchange (RFC 8693):** the provider's server trades a signed-in person's ID token
  for an OpenClear token that acts for them. The token allows only what both the person
  and the API client may do, and the person is created on first use. Link the API client
  to the connection under Admin → API clients.
- **`OpenClear-Act-As-Subject`:** a client can act for someone by the provider's own id
  for them, instead of their email.
- **Upgrading:** run `alembic upgrade head`. It adds the columns
  `sso_connection.customer_claim` and `api_client.sso_connection_id`.

### New: sign-out from the identity provider, and sign-in from its portal (SDK_PLAN.md phase F)

- **OIDC back-channel logout:** when someone signs out at the identity provider, their
  OpenClear sessions end. The provider posts a signed logout token, which is verified
  and accepted only once.
- **Sign-in from a portal tile:** a tile in the identity provider's portal (Okta,
  Entra ID) can start OpenClear sign-in through the connection's "initiate login" URL.
  It runs the normal sign-in with PKCE, and only lands on pages on this site.
- **Where to find the URLs:** each connection's page lists them, along with the
  redirect URI.

### New: SCIM 2.0 provisioning (SDK_PLAN.md phase F)

- **What it does:** a bank's directory (Okta, Entra ID, ...) can create, update and
  deactivate people in OpenClear for an SSO connection, and push its groups, at
  `/scim/v2`.
  - People get access through the connection's existing group mappings.
  - Deactivating someone in the directory, or removing their last mapped group, takes
    their access away and ends their sessions.
  - Nothing is deleted; a deactivated person stays in the action log.
- **Turning it on:** on the connection's page. The page shows the bearer token once.
- **The PATCH formats Okta and Entra ID send** are both handled, including Entra's
  capitalised operations and `"False"` strings.
- **Upgrading:** run `alembic upgrade head`. It adds the tables `scim_user`, `scim_group`
  and `scim_group_member`, and the columns `sso_connection.scim_token_*`.

### New: SAML 2.0 sign-on (SDK_PLAN.md phase F)

- **SAML connections:** a bank, or one of its business customers, can sign in through a
  SAML 2.0 identity provider (ADFS, Okta, Entra ID, Ping, Shibboleth, ...) as well as OIDC.
  It uses the same group mapping, provisioning and "require SSO" rules.
- **Setting one up:** enter the identity provider's entity ID, sign-in URL and signing
  certificate. The connection page then shows OpenClear's entity ID, reply (ACS) URL and
  metadata URL for the identity provider.
- **Security:**
  - OpenClear signs its requests with a key pair generated for each connection.
  - Assertions must be signed with SHA-256 or better, addressed to OpenClear, and within
    their validity window.
  - Each assertion is accepted once.
  - An answer must match the request that this browser started.
- **Sign-in started at the identity provider:** for example, an OpenClear tile in Okta.
  It's off unless the bank turns it on for the connection.
- **Single logout:** a signed logout request from the identity provider ends the person's
  OpenClear sessions.
- **Upgrading:**
  - Run `alembic upgrade head`. It adds new SAML columns to `sso_connection`, makes
    `client_id` and `client_secret_encrypted` optional, and adds the tables `sso_identity`
    and `sso_assertion_seen`.
  - New dependency: `python3-saml`. Its `xmlsec` dependency has prebuilt wheels, so no
    system packages are needed.

### New: plugin matching rules, scoring features and notification channels (PLUGIN_PLAN.md PL3)

- **Matching rules:** a plugin can add checks to check and ACH matching, and each bank
  chooses which ones it uses under Admin → Plugins.
  - A rule can only **add** exceptions, never clear one.
  - A rule that fails flags the item ("A plugin's check failed (review by hand)") rather
    than letting it through.
- **Scoring features:** a plugin can add inputs to the fraud model. They apply to every
  bank, because the shared model is trained on all of them.
- **Notification channels:** a plugin can send a bank's events somewhere else, such as a
  chat tool. Each bank turns a channel on and fills in its settings; secrets are stored
  encrypted and never shown again. A channel gets the same events as webhooks, retried
  if sending fails.
- **Example and guide:** the example plugin has one of each, and
  [docs/PLUGINS.md](docs/PLUGINS.md) covers them.
- **Upgrading:** run `alembic upgrade head`. It adds the column
  `tenant_extension.config_encrypted`.

### New: Python and TypeScript SDKs (SDK_PLAN.md P1)

- **Two client libraries:** `sdks/python` (`openclear-sdk`, Python 3.11+, depends only on
  httpx) and `sdks/typescript` (`@openclear/sdk`, no runtime dependencies; runs on Node 20+,
  Deno, Bun and Workers). Both are licensed **Apache-2.0**, so vendors can ship them.
- **Generated from the API contract:** each SDK's models and endpoint methods are generated
  from `openapi/openclear-v1.json` by `scripts/generate_sdks.py`. A test fails when the
  generated code is out of date.
- **What the hand-written layer adds:**
  - client-credentials tokens, renewed automatically;
  - an `Idempotency-Key` on every write, reused across retries, which cover connection
    failures and responses 429 and 502–504, with `Retry-After` honoured;
  - page iterators;
  - bulk submit, which splits large lists, follows a `202` to its background import and
    returns results in the original order;
  - file upload;
  - decision feed consumption with acknowledgement;
  - Standard Webhooks verification;
  - acting for a person;
  - typed errors.
- **Tests:** both SDKs are tested against a running OpenClear (`tests/test_sdk/`).
- **The API contract now marks file downloads as files:** the CSV, XML and decision-file
  endpoints declare their real content type instead of JSON.

### New: plugins (PLUGIN_PLAN.md PL1)

- **What a plugin can add:** another payment network, OCR provider, email or SMS
  provider, storage backend, scoring model, or bulk import format. It's a separately
  installed package that declares an `openclear.plugins` entry point; OpenClear's code
  doesn't change.
- **Turning one on:** the operator allows it by name with `OPENCLEAR_PLUGINS`.
  Installing the package alone activates nothing.
- **When a plugin is skipped:** if it targets another plugin API version, fails to load,
  or claims a name that's already taken. The app still starts, and the problem shows
  under **Admin → Plugins** and in the log.
- **The built-ins work the same way:** they now register through the same interface.
  Nothing changes for existing installations.
- **Scoring model:** the model is chosen by `OPENCLEAR_ML_SCORING_MODEL`. It defaults
  to `logistic_regression`, so this doesn't change unless you set it.

### New: plugin import formats and decision files, per bank (PLUGIN_PLAN.md PL2)

- **Plugins can add decision file layouts:** the file a core takes back.
  `GET /api/v1/decisions/file?layout=` returns decisions in any layout the bank can use.
  The built-in `openclear_csv` is always available.
- **A bank chooses which plugin formats and layouts it uses:** a plugin's import formats
  and decision layouts are off for each bank until its admin turns them on under
  **Admin → Plugins**. The change is audit-logged.
- **Uploading a whole file through the API:** `POST /api/v1/bulk-uploads` (multipart)
  takes a file in any format the bank can use, built in or from a plugin.
  `GET /api/v1/bulk-uploads/formats` lists them. In the web UI, plugin formats are
  uploaded under **Bulk uploads → Other formats**.
- **`OPENCLEAR_PLUGINS_REQUIRED`:** OpenClear won't start if a plugin listed here
  doesn't load.
- **For plugin authors:**
  - [docs/PLUGINS.md](docs/PLUGINS.md) explains how to write a plugin, and
    `openclear.plugins.api` is the stable set of names a plugin imports;
  - `examples/openclear-plugin-example` is a working template, with a fixed-width
    import format and a fixed-width decision file.
- **Upgrading:** run `alembic upgrade head`. It adds the table `tenant_extension`.

### New: API clients for a bank's systems (SDK_PLAN.md P0)

- **OAuth 2.0 client credentials:** a bank admin creates an API client under Admin → API
  clients. It gets a security group (what it may do) and optionally a customer (whose
  data it may see). The system exchanges the client id and secret at
  `POST /api/v1/oauth/token` for a 30-minute token. Integrations no longer need a
  person's password.
- **A client acts through a service identity:**
  - it gets the same permission checks, customer scope, row-level security and audit
    trail as a member;
  - nobody can sign in as it, by password or SSO, and it can't be attached to by email;
  - it isn't listed or edited with the bank's people.
- **Rotating** a secret stops the old one at once; **revoking** a client stops it on its
  next request. `GET /api/v1/api-clients` lists clients for access reviews.
- **Upgrading:** run `alembic upgrade head` (new table `api_client`, new column
  `user.is_service_account`). Existing Admin groups get `api_client:manage`.

### New: a published API contract (SDK_PLAN.md P0)

- `openapi/openclear-v1.json` is the API's OpenAPI description, committed, and the base for
  generated SDKs (`python scripts/export_openapi.py` regenerates it).
- A test fails on any breaking change to `/api/v1`: a removed endpoint, a newly required
  input, or a removed response field.
- The web pages are no longer listed in the API description.

### New: customer and account sync (SDK_PLAN.md P0)

- `PUT /api/v1/customers/{customer_number}` and `PUT /api/v1/accounts/{account_number}`
  create or update by the bank's own numbers, so a core can sync nightly without lookups.
  Both have paged lists. Customer-scoped callers only touch their own accounts.

### New: webhooks (SDK_PLAN.md P0)

- **Events:** `exception.created`, `exception.decided` and `bulk_upload.completed`, sent to
  endpoints a bank sets up under Admin → Webhooks.
- **Standard Webhooks format:** signed with HMAC-SHA256, with `webhook-id` for
  de-duplication. Payloads carry only ids.
- **Delivery:** retried for about a day. Each endpoint's page shows recent deliveries, sends
  a test event, replays a failed delivery and rotates the signing secret.
- **Safety:**
  - events are written in the same transaction as the change that caused them;
  - URLs must be public https, checked again at connection time, with no redirects
    followed.
  - `OPENCLEAR_WEBHOOK_ALLOW_PRIVATE_HOSTS` is for single-bank installs with internal
    receivers.
- New tables `webhook_endpoint` and `webhook_delivery`. `api_client:manage` now covers
  webhooks too.

### New: the decision feed for cores (SDK_PLAN.md P0)

- **`GET /api/v1/decisions?after=N`:** every pay or return decision in order, with what a
  core needs to act on it: account, check or trace number, amount, date, outcome, return
  reason and ACH return code. Each decision gets the bank's next sequence number when
  it's made, and the numbers become visible in order, so a consumer never skips one.
- Also available as a CSV (`/feed.csv`).
- A consumer **acknowledges** what it processed (`POST /acknowledge`), and
  `GET /consumers` shows how far behind each one is.
- **Upgrading:** `alembic upgrade head` adds `decision.feed_sequence` and numbers existing
  decisions per bank in the order they were made (fraud-training examples excluded). It
  also adds `decision_feed_cursor`.

### New: an API client can act for a user (SDK_PLAN.md P0)

- With the bank's permission per client, a client sends `OpenClear-Act-As: <email>` to act
  for an existing member: an online banking provider acting for its signed-in customer.
- The request has only the permissions both hold. The audit log names the person, plus an
  `api_client.act_as` entry for the client.

### New: Idempotency-Key on API writes (SDK_PLAN.md P0)

- Send `Idempotency-Key` on a POST, PUT, PATCH or DELETE, and a retry gets the first
  response back (`Idempotency-Replayed: true`) instead of doing the work twice.
- The same key with a different request is refused (422). A retry while the first is
  still running gets 409. Server errors aren't kept, so a retry after one runs again.
- Keys are per caller and kept 24 hours (`OPENCLEAR_IDEMPOTENCY_KEY_HOURS`); housekeeping
  removes older ones. New table `idempotency_record`.

## 1.7.0 — 2026-10-09

A scale release: OpenClear can now import large files, train on large histories and run as
several instances, and it reads X9.37 cash letters and NACHA files as banks actually send
them.

### Upgrading from 1.6.0

- Run `alembic upgrade head` (the Docker image does this on startup). New:
  - `bulk_upload_file` status and progress columns;
  - the `bulk_upload_row_result` table;
  - the `audit_log_checkpoint` table.
- **API clients:** a `/bulk` call with more than 1,000 items now answers `202` with an
  upload to poll (`/api/v1/bulk-uploads/{id}`) instead of importing inside the request.
- **ML training** uses the last 365 days of decisions by default
  (`OPENCLEAR_ML_TRAINING_WINDOW_DAYS`; 0 for all history).
- **Nothing else changes until you turn it on:**
  - object storage (`OPENCLEAR_STORAGE_BACKEND=s3`);
  - shared rate limits (`OPENCLEAR_RATE_LIMIT_BACKEND=redis`);
  - housekeeping (`OPENCLEAR_ENABLE_HOUSEKEEPING_SCHEDULER=true`; once on, export
    archives expire after 30 days);
  - audit archiving (`OPENCLEAR_AUDIT_ARCHIVE_AFTER_DAYS`).
- New optional extras: `pip install 'openclear[s3]'`, `'openclear[redis]'`.
- Downgrading to 1.6.0 is supported (`alembic downgrade f7c9e1a3b5d6`); back up first.
  The bulk upload progress and row results are dropped. **If you turned on audit
  archiving, don't downgrade:** 1.6.0 doesn't know about archive checkpoints, so its
  "Verify chain" would report the archived entries as missing.

### Security and small fixes

- **Single sign-on uses PKCE** (S256) for OIDC connections, as current OAuth guidance
  asks even for a server-side client with a secret: an intercepted authorization code is
  useless without the verifier, which stays in the browser's signed sign-on cookie.
- The API documentation reported version 1.5.0; it now reads the installed version.

### Fixes: X9.37 and NACHA files as banks actually send them

Checked against Moov's open source X9 and ACH implementations (Apache-2.0) and their
sample files, which are now test fixtures:
- **X9.37 couldn't read a standard cash letter.** It now reads length-prefixed records
  (the standard) in EBCDIC or ASCII, as well as line-separated files. Fixed field
  positions:
  - the cash letter business date;
  - the 16-digit file total;
  - the Image View Data layout.
- **X9.37 account and check numbers:** the account number is taken from On-Us before the
  `/` symbol, without MICR dashes. The check number comes from Auxiliary On-Us or, failing
  that, after the symbol.
- **X9.37 return cash letters** are now rejected clearly instead of being skipped.
- **NACHA rejected most valid files.** It now:
  - skips the 9s filler lines;
  - counts addenda records in entry/addenda counts;
  - reads files without line breaks;
  - classifies general-ledger and loan transaction codes;
  - reads IAT account numbers;
  - falls back to the file date when the effective date is blank.

  ADV files are refused with a clear message.

### Scalability (SCALE_PLAN.md S4): bulk files import in the background

- **Uploads no longer import inside the web request.** Accounts, issued items, presented
  checks, ACH transactions and check images are stored, checked, and then imported by a
  background job. The upload's page shows progress, then the result: rows that failed
  and why, plus a CSV of every row. A file that can't be parsed is still rejected on the
  form with nothing imported. The dropbox import uses the same path.
- **Faster and safer imports:** 1,000 rows commit at a time, with each row in a savepoint,
  and audit entries are written a chunk at a time. On the test laptop a 50,000-row file
  now takes about 3.5 minutes instead of about 8. An interrupted import resumes where it
  stopped without importing any row twice.
- **One notification per upload:** reviewers get a single "N new exceptions" summary
  instead of one email or text per exception.
- **API:** a `/bulk` call with more than 1,000 items (`OPENCLEAR_BULK_API_SYNC_MAX_ROWS`)
  now answers `202` with an upload to poll at `/api/v1/bulk-uploads/{id}` (and
  `/results`). **Behavior change** for clients that send more than 1,000 items in one call.
- **Large files aren't held in memory:** uploads stream to disk, X9.37 cash letters are
  read through a memory map, and zip images are read as needed.
- **Fixes:**
  - the upload detail page failed for presented-check and fraud-training uploads;
  - a missing or unreadable ML model file made every exception row in an upload fail. It
    now leaves those exceptions unscored, as a failed integrity check already did.
- **Upgrading:** run `alembic upgrade head` (new columns on `bulk_upload_file`, new table
  `bulk_upload_row_result`).

### Scalability (SCALE_PLAN.md S7): data growth

- **Housekeeping:** with `OPENCLEAR_ENABLE_HOUSEKEEPING_SCHEDULER=true`, a daily run
  deletes finished background jobs (after 30 days), sent notifications (180), bulk-upload
  row results (365) and data export archives (30, then the export shows as expired). Each
  period is a setting; 0 keeps them forever. A bank's business records are never touched.
  **Behavior change** once enabled: export archives are no longer downloadable forever.
- **Audit log archiving** (opt-in, `OPENCLEAR_AUDIT_ARCHIVE_AFTER_DAYS`): older entries
  are verified and moved into signed archive files. The audit log page lists and verifies
  them, and they can be downloaded; full data exports include them.
- **CI checks query counts:** bulk imports must stay under 20 statements per row, and list
  pages mustn't make a query per row.
- **Upgrading:** run `alembic upgrade head` (new table `audit_log_checkpoint`).

### Scalability (SCALE_PLAN.md S6): several instances without a shared disk

- **Object storage:** set `OPENCLEAR_STORAGE_BACKEND=s3` and `OPENCLEAR_S3_BUCKET` (plus
  `OPENCLEAR_S3_ENDPOINT_URL` for MinIO, R2 and others) to keep check images, logos, bulk
  uploads, exports and ML models in an S3-compatible bucket. Install with
  `pip install 'openclear[s3]'`. Files already on disk stay readable;
  `scripts/move_files_to_object_storage.py` moves them over.
- **Shared rate limits:** `OPENCLEAR_RATE_LIMIT_BACKEND=redis` and
  `OPENCLEAR_RATE_LIMIT_REDIS_URL` count requests once across every instance. Install
  with `pip install 'openclear[redis]'`. If Redis is down, requests are allowed and the
  error is logged.
- **Connection pools:** `OPENCLEAR_DB_POOL_SIZE` (5), `OPENCLEAR_DB_MAX_OVERFLOW` (10),
  `OPENCLEAR_DB_POOL_TIMEOUT_SECONDS` and `OPENCLEAR_DB_POOL_RECYCLE_SECONDS`. The README
  covers sizing and PgBouncer.
- **Downloads stream:** large export archives and upload originals are no longer read
  into memory to send them.
- **Startup checks:** a production start refuses `s3` without a bucket, or `redis`
  without a URL.

### Scalability (SCALE_PLAN.md S5): ML training at scale

- **Training reads a window:** the last 365 days of decisions
  (`OPENCLEAR_ML_TRAINING_WINDOW_DAYS`, 0 for all history), at most the newest 250,000
  (`OPENCLEAR_ML_TRAINING_MAX_ROWS`). Known-fraud training examples are always included.
  **Behavior change:** decisions older than a year no longer train new models.
- **Less memory:** training reads only the two columns it needs, in batches. Loading
  13,000 decisions now peaks at 14 MB instead of 41 MB.
- **Each model records how it was trained** in `metrics_json["training"]`: rows used, rows
  eligible, whether it was capped, and how long loading and training took.
- **The nightly retrain counts in SQL** instead of loading every decision to count them.
- **Loaded models are capped per process** (`OPENCLEAR_ML_MODEL_CACHE_SIZE`, 256, least
  recently used dropped first).

## 1.6.0 — 2026-10-08

PosPay is now **OpenClear**. This release adds billing (account analysis and hard charge,
with camt.086 export), a durable background job queue, paged API lists, and Postgres
fixes for running at scale.

### Upgrading from 1.5.0

- Run `alembic upgrade head` (the Docker image does this on startup). On a large Postgres
  database, run it in a quiet window: the new indexes block writes to their tables while
  they build.
- **API clients:** list endpoints now return at most 500 rows per call (1,000 with
  `limit`). Follow the `Link` header to read the rest.
- Your `POSPAY_*` settings keep working; see the rename notes below.
- Downgrading to 1.5.0 is supported (`alembic downgrade c3e8f1a5d9b2`).

### Renamed: PosPay is now OpenClear

The project, its Python package (`openclear`), its settings (`OPENCLEAR_*`) and its launcher
(`run_openclear.command` / `.sh` / `.bat`) are renamed. **Existing installs keep working
without changes:**
- `POSPAY_*` settings are still read, from the environment and from `.env`. When both are
  set, `OPENCLEAR_*` wins. Rename them when convenient.
- A local `./pospay.db` is still used if there's no `./openclear.db`.
- The desktop launcher moves `.pospay-run/` to `.openclear-run/` on its first run (your
  database, keys and uploads come with it). It rebuilds its Python environment, which
  takes a minute.
- Platform API keys issued before the rename (`pospay_platform_...`) still work. New keys
  start `openclear_platform_`.
- A saved light/dark theme carries over.
- Kept as they are, because they identify existing deployments: the demo's Fly app and
  hostname (`pospay-demo`; WebAuthn keys are bound to it), its volume, and the Docker
  image's database path `/data/pospay.db`.
- If you import the package in your own scripts, change `pospay` to `openclear`.
- `docker-compose.yml` (development only) now names its Postgres user, database and
  volume `openclear`, so a local compose database starts fresh.

### Scalability (SCALE_PLAN.md S0, S1, S1a)

- **Postgres fixes:**
  - a fresh Postgres database can now be migrated;
  - authenticated API requests no longer fail on Postgres;
  - row-level security works end to end for a restricted app role. That covers login,
    requests, scheduled jobs, switching organization, usage metrics and creating a bank.
  - Connect the app as a role without `BYPASSRLS` (see README, "Postgres").
- **The audit log's hash chain is safe under concurrent writers:** a per-bank sequence
  number, a per-bank lock on Postgres, and a retry.
- **Tests:** the suite runs against Postgres in CI (`OPENCLEAR_TEST_DATABASE_URL`), and the
  CI failures on Linux / Python 3.11 are fixed.
- **Tools:** `scripts/perf/` has a volume generator and a timing script.
- **API list endpoints are paged** (S2): `limit` (default 500, at most 1,000) and
  `offset`, oldest first, with `X-Total-Count` and a `Link: rel="next"` header (see
  API.md). **Behavior change:** clients that read more than 500 rows from one call must
  now follow `Link`.
- **Faster hot paths** (S2): new indexes for the duplicate-paid check, stop-payment
  matching, the exceptions queue, the disposition sweep, the ACH frequency check and
  notification dispatch. The ACH frequency check counts in SQL. The queue pages load
  source items in one query per network, and customer exports filter decisions in SQL.
  On a large existing Postgres database, run the upgrade in a quiet window: building the
  indexes blocks writes to those tables while it runs.
- **Background jobs** (S3): OCR, data exports, dropbox scans and retraining are jobs in
  a durable queue (the `job` table) instead of work done inside the web request. They're
  retried with back-off, and picked up again if a worker dies. By default each web process
  runs a worker thread, so nothing changes for single-process installs. To size workers
  separately, set `OPENCLEAR_WORKER_MODE=external` and run `openclear-worker`. The platform
  operator can see and retry failed jobs at `/api/v1/platform/jobs`, with a platform key
  carrying the new `operations` scope.
- **Exports have no row limit** (S3): they stream to disk in batches. The
  `data_export_max_rows_per_entity` setting is gone, and the default time limit is now an
  hour instead of 5 minutes.
- **Over-long text** is now a form message (or a `400` from the API) instead of an error
  page on Postgres and SQL Server.

### New: billing (BILLING_PLAN.md)

- **A bank can bill its customers for positive pay.** The fee schedule draws on 24
  metrics: per customer, user, account, issued item or file, item evaluated, exception,
  item returned, stop payment, image, notification and more. Each fee has a unit price,
  optional step tiers, a monthly minimum and the bank's AFP Service Code. It's under
  Billing in the Organization menu and at `/api/v1/billing`.
- **Each customer is on account analysis, hard charge or exempt**, with a bank-wide
  default. The bank can also give a customer a discount, a negotiated price or a waiver
  on any fee.
- **Statements:** live for any period, with volumes per account, plus a PDF. Finalizing a
  month freezes it, and a replacement version can follow.
- **Exports** from a finalized month: the billing report (CSV), an analysis feed (CSV), a
  hard-charge posting file for the core (CSV), and ISO 20022 camt.086.
- **Customer users** with `billing:read` see their own statement.
- **Platform billing:** the platform operator can invoice each bank at
  `/api/v1/platform/billing`, with a platform key carrying the new `billing` scope.
- **Upgrading:** run `alembic upgrade head`. Existing Admin security groups get
  `billing:read` and `billing:manage`. Nothing is charged until a bank adds fees.

## 1.5.0 — 2026-09-23

A security, reliability and usability release. It fixes every finding from the September
2026 review ([EVALUATION.md](EVALUATION.md)). It also lets each bank choose which fraud
model scores its exceptions, and it adds new screens for deciding exceptions.

### Upgrading from 1.0.0

- Run `alembic upgrade head`. The Docker image does this on startup. The new migrations
  only add columns and fill in values for existing rows.
- **Existing banks stay on the shared fraud model**, with nothing to do. New banks start
  on it too, and can switch to a bank-only model after 90 days.
- **Model files are now fingerprinted.** The upgrade records each existing model file's
  SHA-256. A model whose file can't be found during the upgrade isn't loaded until it's
  retrained. Its exceptions go unscored in the meantime, and it's logged.
- **Several instances:** read "Running more than one instance" in the README first.
  Several instances against one database are supported on Postgres only, and they need a
  shared volume for files.
- The public demo organization now resets every hour, and some destructive actions are
  locked there.

### Security

- A customer-scoped user could export the whole bank's data. Exports are now limited to
  the caller's own customer.
- Changing a user's security group now takes effect on their next request, not when
  their token expires.
- Logging out, changing a password, and an admin's "sign out everywhere" now end sessions
  on the server.
- SSO issuer URLs can't reach private or internal addresses, and the OIDC `state` is
  verified.
- Uploaded SVG logos and bulk-upload downloads are served safely.
- Only the platform operator can train or activate the shared fraud model. Banks control
  only their own models. A bank's fraud-training examples reach the shared model only after
  the operator approves them.
- Model files are verified against their recorded SHA-256 before they're loaded.
- The development `docker-compose.yml` publishes Postgres on localhost only.

### New

- **Fraud model choice per bank:** each bank uses the shared model or its own model.
  Switching copies the shared model, so scoring never stops. New versions replace the
  active model only if they do at least as well on the same recent decisions. See
  Settings → Fraud scoring, and the platform operator API.
- **Exception review:** a detail page that shows the evidence (images, OCR, the matching
  issued item, which checks failed and why), with no default choice on the decision
  forms. The queue opens on items that need attention. There's also an Approvals queue for
  maker/checker review, with a count in the navigation.
- **Password lifecycle:** people can change their own password, admins can reset one, and
  a password change can be required at next login.
- **Sessions:** activity keeps a session alive, and the page warns 5 minutes before it
  would expire.
- A dashboard built around what needs doing.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): how OpenClear fits together, and why.

### Fixes

- X9.37 files are checked against their bundle control totals as well as their cash
  letter and file totals. If any total doesn't match, the whole file is rejected.
- On Postgres, scheduled jobs run on one instance at a time.
- Errors show friendly pages and form messages instead of bare 500s or raw database text.
- Pages no longer scroll sideways on phones. The settings and admin pages were tidied.
