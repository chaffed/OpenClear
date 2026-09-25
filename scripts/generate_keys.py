#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Generates the four ECDSA P-256 key pairs OpenClear signs with — one each for JWTs,
bulk-upload file signing, the audit log hash chain, and signed WSUD statements (see
README.md's "Signing keys" section for the full picture). Run this once per real
deployment:

    python scripts/generate_keys.py --output-dir keys

then point the printed OPENCLEAR_* env vars at the resulting files and set
OPENCLEAR_ENVIRONMENT=production. Never run this against dev_keys/ — that directory is a
deliberately public, checked-in key pair for local dev and the test suite only.

Deliberately stdlib+cryptography only (no openclear import) — this needs to run before the
app's own settings/config are even relevant, same spirit as scripts/launcher.py."""

import argparse
import os
import stat
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

_KEY_NAMES = ("jwt", "file_signing", "audit_log_signing", "wsud_signing")


def _generate_pair(output_dir: Path, name: str, *, force: bool) -> None:
    private_path = output_dir / f"{name}_private.pem"
    public_path = output_dir / f"{name}_public.pem"

    if not force and (private_path.exists() or public_path.exists()):
        print(f"  Skipping {name}: {private_path.name}/{public_path.name} already exist (use --force to overwrite)")
        return

    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    private_path.write_bytes(private_pem)
    public_path.write_bytes(public_pem)
    # Best-effort on POSIX; os.chmod is a no-op-ish on Windows but doesn't raise.
    try:
        os.chmod(private_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    print(f"  Generated {name}: {private_path} (chmod 600), {public_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", default="keys", help="Directory to write the 8 PEM files into (default: keys/)")
    parser.add_argument("--force", action="store_true", help="Overwrite existing key files")
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    if output_dir.name == "dev_keys":
        print("Refusing to write into a directory named 'dev_keys' — that's the checked-in, public test key pair.")
        sys.exit(1)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Generating ECDSA P-256 key pairs in {output_dir}...\n")
    for name in _KEY_NAMES:
        _generate_pair(output_dir, name, force=args.force)

    print(
        "\nSet these before starting the app:\n"
        f"  OPENCLEAR_JWT_PRIVATE_KEY_PATH={output_dir / 'jwt_private.pem'}\n"
        f"  OPENCLEAR_JWT_PUBLIC_KEY_PATH={output_dir / 'jwt_public.pem'}\n"
        f"  OPENCLEAR_FILE_SIGNING_PRIVATE_KEY_PATH={output_dir / 'file_signing_private.pem'}\n"
        f"  OPENCLEAR_FILE_SIGNING_PUBLIC_KEY_PATH={output_dir / 'file_signing_public.pem'}\n"
        f"  OPENCLEAR_AUDIT_LOG_SIGNING_PRIVATE_KEY_PATH={output_dir / 'audit_log_signing_private.pem'}\n"
        f"  OPENCLEAR_AUDIT_LOG_SIGNING_PUBLIC_KEY_PATH={output_dir / 'audit_log_signing_public.pem'}\n"
        f"  OPENCLEAR_WSUD_SIGNING_PRIVATE_KEY_PATH={output_dir / 'wsud_signing_private.pem'}\n"
        f"  OPENCLEAR_WSUD_SIGNING_PUBLIC_KEY_PATH={output_dir / 'wsud_signing_public.pem'}\n"
        "  OPENCLEAR_ENVIRONMENT=production\n"
        "\n"
        "Also set a real random OPENCLEAR_SSO_ENCRYPTION_KEY (a plain random string, not a "
        "key pair — it's for encrypting stored SSO client secrets, not signing).\n"
        "\n"
        "Rotating any of these keys later invalidates every active login session (JWT "
        "key) or stops previously-signed files/audit entries/WSUD statements from "
        "re-verifying (file signing / audit log / WSUD signing keys) — expected, "
        "one-time costs of a rotation, not bugs."
    )


if __name__ == "__main__":
    main()
