# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The host's software currency check (services/control_checks.py::software_currency;
services/release_feed.py), against a release feed in GitHub's shape."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from openclear.config import get_settings
from openclear.services import control_checks, release_feed


def _feed(monkeypatch, releases: list[dict], *, running: str = "1.18.0", status: int = 200):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return httpx.Response(status, json=releases, request=httpx.Request("GET", url))

    monkeypatch.setattr(release_feed.httpx, "get", fake_get)
    monkeypatch.setattr(get_settings(), "release_feed_url", "https://releases.example/api")
    monkeypatch.setattr("importlib.metadata.version", lambda name: running)
    release_feed.forget()
    return calls


def _release(tag: str, days_ago: int, **extra) -> dict:
    return {"tag_name": tag, "published_at": (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z"), **extra}


@pytest.fixture(autouse=True)
def _forget():
    yield
    release_feed.forget()


def test_current_behind_and_far_behind(monkeypatch, db_session):
    _feed(monkeypatch, [_release("v1.18.0", 60), _release("v1.19.0", 10), _release("v1.20.0-rc1", 1, prerelease=True)])
    current = control_checks.software_currency(db_session, None)
    assert current.status == "PASS" and "1 newer release, the first (v1.19.0) out 10 days, within 30" in current.detail

    _feed(monkeypatch, [_release("v1.18.0", 100), _release("v1.19.0", 45)])
    assert control_checks.software_currency(db_session, None).status == "WARN"
    _feed(monkeypatch, [_release("v1.18.0", 400), _release("v1.19.0", 200), _release("v1.20.0", 20)])
    behind = control_checks.software_currency(db_session, None)
    assert behind.status == "FAIL" and behind.evidence["latest"] == "v1.20.0" and len(behind.evidence["newer"]) == 2

    _feed(monkeypatch, [_release("v1.19.0", 3)], running="1.19.0")
    assert control_checks.software_currency(db_session, None).detail == "Running 1.19.0, the latest release."
    _feed(monkeypatch, [_release("v1.19.0", 3)], running="1.20.0.dev1+local")
    assert "isn't a published release" in control_checks.software_currency(db_session, None).detail


def test_the_feed_is_cached_and_a_failure_doesnt_hang_each_view(monkeypatch, db_session):
    calls = _feed(monkeypatch, [_release("v1.18.0", 3)])
    control_checks.software_currency(db_session, None)
    control_checks.software_currency(db_session, None)
    assert len(calls) == 1  # kept for 12 hours

    calls = _feed(monkeypatch, {"message": "rate limited"}, status=403)
    failed = control_checks.software_currency(db_session, None)
    assert failed.status == "WARN" and "Couldn't read the release feed" in failed.detail
    control_checks.software_currency(db_session, None)
    assert len(calls) == 1  # the failure is remembered too

    monkeypatch.setattr(get_settings(), "release_feed_url", "")
    off = control_checks.software_currency(db_session, None)
    assert off.status == "WARN" and "No release feed is set" in off.detail
