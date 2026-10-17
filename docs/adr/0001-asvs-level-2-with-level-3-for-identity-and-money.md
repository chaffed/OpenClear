# ASVS 5.0 Level 2, with Level 3 where identity and money are at stake

OpenClear measures itself against OWASP ASVS 5.0 at Level 2, and at Level 3 in V6
Authentication, V7 Session, V8 Authorization, V9 Tokens, V10 OAuth/OIDC, V11 Cryptography and
V16 Logging. Banks deciding payments expect at least Level 2, and those chapters are where an
attacker would move money or act as someone else. Level 3 everywhere was rejected: much of it
(HSM-backed keys, for example) is the infrastructure of whoever runs the installation, so
those requirements are recorded as CUECs rather than claimed.

Once this level is published to banks, lowering it is a visible step back, so changing it
needs its own decision rather than a quiet edit to `docs/compliance/asvs.md`.
