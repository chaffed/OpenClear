#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""One-click local launcher for OpenClear (SQLite, zero external services).

Run this directly (`python3 scripts/launcher.py`) or via the platform wrapper
(run_openclear.command on macOS): it creates a virtual environment if needed, installs the
project into it, runs database migrations, prompts for first-run setup info if the
database is empty, then starts the server and opens your browser.

Everything this script creates — the virtual environment, the SQLite database, uploaded
check images, and trained ML model files — lives under .openclear-run/ next to this project,
never inside the source tree itself. To fully reset (wipe the database, uploads, trained
models, and installed dependencies) and start over, just delete that one folder:

    rm -rf .openclear-run

Safe to re-run any time otherwise — every step is idempotent and skips whatever's
already done.

Deliberately stdlib-only until AFTER the re-exec into the venv's own interpreter (see
_create_venv_and_install/_reexec_under_venv below): this script must run under whatever
bare Python the user has, before any of OpenClear's dependencies exist anywhere.
"""

import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUN_DIR = PROJECT_ROOT / ".openclear-run"  # delete this one folder to fully reset — see module docstring
VENV_DIR = RUN_DIR / "venv"
# Written only after a fully successful install, holding a hash of pyproject.toml at
# install time (see _pyproject_fingerprint/_create_venv_and_install) — an interrupted
# first run (Ctrl+C mid-`pip install`) leaves a venv directory behind but no marker, so
# the next run recreates it from scratch rather than trusting a half-installed
# environment; a marker whose hash no longer matches the current pyproject.toml (e.g.
# after `git pull` brings in a commit that added a dependency) triggers a dependency
# refresh instead, without wiping the existing venv.
VENV_MARKER = VENV_DIR / ".openclear-setup-complete"

HOST = "127.0.0.1"  # never a LAN IP: WebAuthn requires a secure context, and this is a
PORT = 8000  # local single-user tool, not something that should be network-reachable.


def _venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def _is_running_under_venv() -> bool:
    # NOT `Path(sys.executable).resolve() == _venv_python().resolve()`: venv-created
    # interpreters are frequently symlinks back to the base Python (common on macOS/
    # Linux), so resolving symlinks makes the venv's python compare equal to the system
    # python too. sys.prefix is the standard, symlink-proof way to detect which
    # environment is actually active (it points at the venv root either way).
    return Path(sys.prefix).resolve() == VENV_DIR.resolve()


def _pyproject_fingerprint() -> str:
    import hashlib

    # The lock too: a re-lock (scripts/lock.sh) must reinstall even when pyproject.toml is unchanged.
    return hashlib.sha256(
        (PROJECT_ROOT / "pyproject.toml").read_bytes() + (PROJECT_ROOT / "requirements" / "runtime.txt").read_bytes()
    ).hexdigest()


def _create_venv_and_install() -> None:
    fingerprint = _pyproject_fingerprint()
    if VENV_MARKER.exists() and VENV_MARKER.read_text().strip() == fingerprint:
        return  # already installed, and pyproject.toml hasn't changed since

    venv_python = str(_venv_python())
    if VENV_MARKER.exists():
        # A complete, previously-installed venv — pyproject.toml just changed since (a
        # `git pull` bringing in a commit that added a dependency, most commonly), so
        # this only needs its dependencies refreshed, not a full recreate.
        print("OpenClear's dependencies have changed since your last run — updating...\n")
    else:
        print("Setting up OpenClear for the first time — this can take a minute...\n")
        if VENV_DIR.exists():
            # No marker but a venv dir exists — a previous run was interrupted mid-install
            # (e.g. Ctrl+C during `pip install`), so it can't be trusted; start clean.
            print(f"  Removing incomplete virtual environment at {VENV_DIR}")
            shutil.rmtree(VENV_DIR)

        print(f"  Creating virtual environment at {VENV_DIR}")
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        venv.create(str(VENV_DIR), with_pip=True)

        print("  Upgrading pip...")
        subprocess.run([venv_python, "-m", "pip", "install", "--quiet", "--upgrade", "pip"], check=True)

    print("  Installing OpenClear and its dependencies (this is the slow part)...")
    # Only what the hashed lock names (OWASP_PLAN.md OW-D9), then OpenClear itself without
    # letting pip resolve anything further.
    lock = PROJECT_ROOT / "requirements" / "runtime.txt"
    subprocess.run([venv_python, "-m", "pip", "install", "--quiet", "--require-hashes", "-r", str(lock)], check=True)
    subprocess.run([venv_python, "-m", "pip", "install", "--quiet", "--no-deps", "-e", str(PROJECT_ROOT)], check=True)

    VENV_MARKER.write_text(fingerprint)
    print("  Setup complete.\n")


def _reexec_under_venv() -> None:
    # os.execv() replaces the process image immediately with no cleanup — anything
    # buffered on stdout (block-buffering kicks in whenever output isn't a live TTY,
    # e.g. redirected to a log file) would otherwise be silently dropped rather than
    # ever reaching whatever's capturing this script's output.
    sys.stdout.flush()
    sys.stderr.flush()
    venv_python = str(_venv_python())
    os.execv(venv_python, [venv_python, str(Path(__file__).resolve()), *sys.argv[1:]])


def _configure_run_env() -> None:
    """Points the app's database, check-image storage, tenant branding assets, saved bulk
    upload files, and ML artifacts at .openclear-run/ instead of their normal
    project-root-relative defaults (used by a manual `alembic upgrade head && uvicorn ...`
    dev setup) — this is what keeps the quickstart from ever writing into the checked-out
    source tree. setdefault() so an advanced user's own OPENCLEAR_* env vars still win. Must
    run before the first `openclear.config.get_settings()` call anywhere (it's @lru_cache'd),
    so this is called before any `from openclear...` import."""
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    # Relaxes config.py::assert_production_safe's checks — this launcher is explicitly a
    # local, single-user, SQLite-only tool (see the module docstring), so the checked-in
    # dev/test signing keys and default SSO encryption key are fine here.
    os.environ.setdefault("OPENCLEAR_ENVIRONMENT", "development")
    os.environ.setdefault("OPENCLEAR_DATABASE_URL", f"sqlite:///{RUN_DIR / 'openclear.db'}")
    os.environ.setdefault("OPENCLEAR_CHECK_IMAGE_STORAGE_DIR", str(RUN_DIR / "check_images"))
    os.environ.setdefault("OPENCLEAR_TENANT_ASSET_STORAGE_DIR", str(RUN_DIR / "tenant_assets"))
    os.environ.setdefault("OPENCLEAR_BULK_UPLOAD_STORAGE_DIR", str(RUN_DIR / "bulk_uploads"))
    os.environ.setdefault("OPENCLEAR_ML_ARTIFACT_DIR", str(RUN_DIR / "ml_artifacts"))


def _check_tesseract() -> None:
    if shutil.which("tesseract") is None:
        print(
            "NOTE: Tesseract (used for check-image OCR) was not found on your PATH.\n"
            "      OpenClear will still run — uploading a check image just won't extract\n"
            "      an amount/payee until you install it:\n"
            "        macOS:  brew install tesseract\n"
            "        Linux:  apt install tesseract-ocr\n"
        )


def _ensure_pdf_extra_installed() -> None:
    """Installs the "pdf" extra (weasyprint, for the documentation PDF export) if it's
    not already present — separate from _create_venv_and_install's one-time setup so an
    environment created by an *older* version of this launcher (before this extra
    existed) also picks it up, without paying the cost of reinstalling every dependency
    on every single launch. The `pip show` check keeps this a fast no-op on every run
    after the first one that actually needs to install something."""
    venv_python = str(_venv_python())
    already_installed = subprocess.run(
        [venv_python, "-m", "pip", "show", "weasyprint"], capture_output=True
    ).returncode == 0
    if already_installed:
        return
    print("  Installing PDF export support...")
    subprocess.run([venv_python, "-m", "pip", "install", "--quiet", "-e", f"{PROJECT_ROOT}[pdf]"], check=True)


def _check_weasyprint() -> None:
    # Mirrors _check_tesseract()'s soft-warn posture: weasyprint (the pip package) is
    # always installed by _ensure_pdf_extra_installed() above, but *importing* it can
    # still raise OSError if the host is missing the underlying Pango/Cairo/GLib system
    # libraries, which pip can't install for you.
    from openclear.services.doc_pdf_service import weasyprint_usable

    if not weasyprint_usable():
        print(
            "NOTE: PDF export (in the documentation pages) needs Pango, Cairo, and GLib\n"
            "      on your system, which weren't found. OpenClear will still run —\n"
            "      everything else works normally — until you install them:\n"
            "        macOS:  brew install pango\n"
            "        Linux:  apt install libpango-1.0-0 libcairo2\n"
        )


def _run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    print("Applying database migrations...")
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    command.upgrade(cfg, "head")


def _prompt_first_run_setup() -> None:
    import getpass

    from sqlalchemy import select

    from openclear.db.session import get_session_factory
    from openclear.domain.tenant import Tenant
    from openclear.services.provisioning_service import create_tenant_with_admin

    session = get_session_factory()()
    try:
        if session.execute(select(Tenant.id).limit(1)).first() is not None:
            return  # already provisioned on a prior run — nothing to do

        print("No organization set up yet — let's create one.\n")
        tenant_name = input("Organization name (e.g. 'Acme Corp'): ").strip()
        default_slug = (tenant_name or "acme").lower().replace(" ", "-")
        tenant_slug = input(f"URL slug [{default_slug}]: ").strip() or default_slug
        admin_email = input("Admin email: ").strip()

        admin_password = getpass.getpass("Admin password (min 8 characters): ")
        while len(admin_password) < 8:
            print("Please choose a password of at least 8 characters.")
            admin_password = getpass.getpass("Admin password (min 8 characters): ")

        confirm_password = getpass.getpass("Confirm password: ")
        while confirm_password != admin_password:
            print("Passwords didn't match — try again.")
            admin_password = getpass.getpass("Admin password (min 8 characters): ")
            confirm_password = getpass.getpass("Confirm password: ")

        create_tenant_with_admin(
            session,
            tenant_name=tenant_name,
            tenant_slug=tenant_slug,
            admin_email=admin_email,
            admin_password=admin_password,
        )
        session.commit()
        print(f"\nCreated organization '{tenant_name}' (slug: {tenant_slug}) with admin {admin_email}.\n")
    finally:
        session.close()


def _open_browser_when_ready(url: str) -> None:
    import socket
    import threading
    import time
    import webbrowser

    def _wait_and_open() -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((HOST, PORT), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.2)
        webbrowser.open(url)

    threading.Thread(target=_wait_and_open, daemon=True).start()


LEGACY_POSPAY_RUN_DIR = PROJECT_ROOT / ".pospay-run"


def _adopt_legacy_run_dir() -> None:
    """The project was called PosPay before 1.6 and kept everything under .pospay-run/.
    Move that data (database, uploads, model files, keys) to the new folder instead of
    starting over. The old virtual environment isn't moved (its scripts hold absolute
    paths, and it has the old package installed), so it's rebuilt on this run."""
    if not LEGACY_POSPAY_RUN_DIR.is_dir() or RUN_DIR.exists():
        return
    print(f"Moving your existing data from {LEGACY_POSPAY_RUN_DIR.name}/ to {RUN_DIR.name}/ (the project is now OpenClear)...")
    shutil.rmtree(LEGACY_POSPAY_RUN_DIR / "venv", ignore_errors=True)
    LEGACY_POSPAY_RUN_DIR.rename(RUN_DIR)
    legacy_db = RUN_DIR / "pospay.db"
    if legacy_db.exists() and not (RUN_DIR / "openclear.db").exists():
        legacy_db.rename(RUN_DIR / "openclear.db")


def main() -> None:
    os.chdir(PROJECT_ROOT)  # pin cwd regardless of how this was invoked (double-click,
    # `python3 scripts/launcher.py` from elsewhere, etc.) — everything else uses absolute paths anyway
    _adopt_legacy_run_dir()

    if not _is_running_under_venv():
        _create_venv_and_install()
        _reexec_under_venv()
        return  # unreachable: execv replaces this process

    _configure_run_env()
    _check_tesseract()
    _ensure_pdf_extra_installed()
    _check_weasyprint()
    _run_migrations()
    _prompt_first_run_setup()

    url = f"http://{HOST}:{PORT}/ui/login"
    print(f"Starting OpenClear at {url}")
    print(f"Everything this run creates lives under {RUN_DIR} — delete that folder to fully reset.")
    print("Press Ctrl+C to stop.\n")
    _open_browser_when_ready(url)

    import uvicorn

    from openclear.main import app

    try:
        uvicorn.run(app, host=HOST, port=PORT)
    except OSError as exc:
        print(f"\nCouldn't start the server: {exc}")
        print(f"Something may already be using port {PORT} — close it and try again.")
        sys.exit(1)


if __name__ == "__main__":
    main()
