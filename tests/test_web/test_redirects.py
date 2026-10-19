# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""Every redirect goes through web/redirects.py (CODE_SCANNING_PLAN.md CS2): `see_other` takes
only a local path, and the three that leave the site go to the bank's identity provider."""

import ast
from pathlib import Path

import pytest

from openclear.web.redirects import FALLBACK, is_local, see_other

SRC = Path(__file__).resolve().parents[2] / "src" / "openclear"


def test_no_route_builds_a_redirect_itself():
    found = []
    for path in SRC.rglob("*.py"):
        if path.name == "redirects.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "RedirectResponse":
                found.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert found == [], "Use web/redirects.py::see_other (or to_identity_provider): " + ", ".join(found)


@pytest.mark.asvs("3.7.2")
@pytest.mark.parametrize("url", ["https://evil.example/", "//evil.example", "/\\evil.example", "/ui/\tx", "javascript:alert(1)",
                                 "ui/relative", ""])
def test_anything_but_a_local_path_goes_to_the_dashboard(url):
    assert not is_local(url)
    assert see_other(url).headers["location"] == FALLBACK


def test_a_local_path_with_a_query_is_kept():
    assert see_other("/ui/exceptions?status=all&flash=Done.").headers["location"] == "/ui/exceptions?status=all&flash=Done."
    assert see_other("/ui/x", status_code=302).status_code == 302
