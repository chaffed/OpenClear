# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The project was called PosPay before 1.6. Existing deployments and local installs must
keep working after the rename to OpenClear: old POSPAY_* settings, the old SQLite file, the
launcher's old data folder, and a saved theme."""

import importlib.util
from pathlib import Path

from openclear.config import Settings


def _clear(monkeypatch, name):
    monkeypatch.delenv(f"OPENCLEAR_{name}", raising=False)
    monkeypatch.delenv(f"POSPAY_{name}", raising=False)


def test_legacy_pospay_settings_are_still_read(monkeypatch):
    _clear(monkeypatch, "RATE_LIMIT_PER_MINUTE")
    monkeypatch.setenv("POSPAY_RATE_LIMIT_PER_MINUTE", "77")
    assert Settings().rate_limit_per_minute == 77


def test_openclear_settings_win_over_legacy_ones(monkeypatch):
    monkeypatch.setenv("POSPAY_RATE_LIMIT_PER_MINUTE", "77")
    monkeypatch.setenv("OPENCLEAR_RATE_LIMIT_PER_MINUTE", "88")
    assert Settings().rate_limit_per_minute == 88


def test_an_existing_pospay_sqlite_file_is_kept(monkeypatch, tmp_path):
    _clear(monkeypatch, "DATABASE_URL")
    monkeypatch.chdir(tmp_path)
    assert Settings().database_url == "sqlite:///./openclear.db"
    (tmp_path / "pospay.db").write_bytes(b"")
    assert Settings().database_url == "sqlite:///./pospay.db"
    (tmp_path / "openclear.db").write_bytes(b"")
    assert Settings().database_url == "sqlite:///./openclear.db"


def _load_launcher(monkeypatch, root: Path):
    spec = importlib.util.spec_from_file_location("launcher_under_test", Path(__file__).resolve().parents[2] / "scripts" / "launcher.py")
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    monkeypatch.setattr(launcher, "RUN_DIR", root / ".openclear-run")
    monkeypatch.setattr(launcher, "LEGACY_POSPAY_RUN_DIR", root / ".pospay-run")
    return launcher


def test_the_launcher_moves_the_old_data_folder_and_drops_the_old_venv(monkeypatch, tmp_path):
    old = tmp_path / ".pospay-run"
    (old / "venv" / "bin").mkdir(parents=True)
    (old / "keys").mkdir()
    (old / "keys" / "jwt_private.pem").write_text("key")
    (old / "pospay.db").write_text("db")
    launcher = _load_launcher(monkeypatch, tmp_path)

    launcher._adopt_legacy_run_dir()

    new = tmp_path / ".openclear-run"
    assert not old.exists()
    assert (new / "openclear.db").read_text() == "db"
    assert (new / "keys" / "jwt_private.pem").read_text() == "key"
    assert not (new / "venv").exists()


def test_the_launcher_leaves_an_existing_new_folder_alone(monkeypatch, tmp_path):
    (tmp_path / ".pospay-run").mkdir()
    (tmp_path / ".openclear-run").mkdir()
    launcher = _load_launcher(monkeypatch, tmp_path)

    launcher._adopt_legacy_run_dir()

    assert (tmp_path / ".pospay-run").exists()


def test_a_theme_saved_under_the_old_cookie_name_still_applies(client):
    client.cookies.set("pospay_theme", "dark")
    page = client.get("/ui/login")
    assert 'data-theme="dark"' in page.text
