# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""Builds the bundled breached-password list (OWASP_PLAN.md OW-D15) from a text list of
passwords, one per line: src/openclear/auth/data/breached-passwords.bin holds the first 8
bytes of each password's SHA-1, sorted and de-duplicated, so the passwords themselves are
never shipped. auth/breached_passwords.py searches it.

The bundled file is built from the UK NCSC's 100,000 most common passwords (derived from
Have I Been Pwned), as published in SecLists (MIT License, Copyright (c) 2018 Daniel Miessler):
Passwords/Common-Credentials/100k-most-used-passwords-NCSC.txt

    python scripts/build_breached_list.py <passwords.txt>
"""

import hashlib
import sys
from pathlib import Path

PREFIX_BYTES = 8
OUT = Path(__file__).resolve().parent.parent / "src" / "openclear" / "auth" / "data" / "breached-passwords.bin"


def main(source: str) -> int:
    lines = Path(source).read_text(encoding="utf-8", errors="replace").splitlines()
    prefixes = sorted({hashlib.sha1(p.encode("utf-8")).digest()[:PREFIX_BYTES] for p in lines if p})
    OUT.write_bytes(b"".join(prefixes))
    print(f"wrote {OUT.name}: {len(prefixes)} entries, {OUT.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
