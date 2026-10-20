# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""Every redirect goes through web/redirects.py (CODE_SCANNING_PLAN.md CS2): `see_other` takes
only a local path, and the three that leave the site go to the bank's identity provider."""

import ast
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from openclear.web.redirects import FALLBACK, is_local, see_other
from tests.test_web.test_web_full_flow import _login

SRC = Path(__file__).resolve().parents[2] / "src" / "openclear"

# `location` headers that aren't redirects: SCIM answers 201 Created with the new resource's address.
NOT_REDIRECTS = {"services/scim_service.py"}


def _name(node: ast.expr) -> str | None:
    return node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else None


def _is_location(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.lower() == "location"


def _redirects_made_by_hand(tree: ast.AST) -> list[tuple[int, str]]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _name(node.func) == "RedirectResponse":
            found.append((node.lineno, "RedirectResponse(...)"))
        elif isinstance(node, ast.ImportFrom) and any(a.name == "RedirectResponse" and a.asname for a in node.names):
            found.append((node.lineno, "RedirectResponse imported under another name"))
        elif isinstance(node, ast.Dict) and any(_is_location(key) for key in node.keys):
            found.append((node.lineno, "a location header"))
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store) and _is_location(node.slice):
            found.append((node.lineno, "a location header"))
    return found


def test_no_route_builds_a_redirect_itself():
    found = []
    for path in SRC.rglob("*.py"):
        relative = path.relative_to(SRC).as_posix()
        if relative == "web/redirects.py" or relative in NOT_REDIRECTS:
            continue
        found += [f"{relative}:{line} {what}" for line, what in _redirects_made_by_hand(ast.parse(path.read_text()))]
    assert found == [], "Use web/redirects.py::see_other (or to_identity_provider): " + ", ".join(found)


@pytest.mark.parametrize("code", [
    "return RedirectResponse('/x')",
    "return responses.RedirectResponse('/x')",
    "from fastapi.responses import RedirectResponse as Go",
    "return Response(status_code=302, headers={'Location': 'https://evil.example/'})",
    "response.headers['location'] = '/x'",
])
def test_the_guard_sees_each_way_of_making_a_redirect(code):
    assert _redirects_made_by_hand(ast.parse(code))


@pytest.mark.asvs("3.7.2")
@pytest.mark.parametrize("url", ["https://evil.example/", "//evil.example", "/\\evil.example", "/ui/\tx", "javascript:alert(1)",
                                 "ui/relative", ""])
def test_anything_but_a_local_path_goes_to_the_dashboard(url):
    assert not is_local(url)
    assert see_other(url, flash="Saved.").headers["location"] == FALLBACK


def test_a_local_path_with_a_query_is_kept():
    assert see_other("/ui/exceptions?status=all").headers["location"] == "/ui/exceptions?status=all"
    assert see_other("/ui/x").status_code == 303


def test_the_query_is_encoded_and_reads_back_as_written():
    message = "Saved: a & b = c, 50% #1 / café?"
    location = see_other("/ui/exceptions?status=all", flash=message, error=None, event="exception-decided").headers["location"]
    parts = urlsplit(location)
    assert parts.path == "/ui/exceptions" and not parts.fragment
    assert parse_qs(parts.query) == {"status": ["all"], "flash": [message], "event": ["exception-decided"]}
    assert see_other("/ui/x", flash=None).headers["location"] == "/ui/x"


def test_a_query_value_cant_make_the_redirect_leave_the_site():
    location = see_other("/ui/login", next="//evil.example/\r\nSet-Cookie: x=1").headers["location"]
    assert location.startswith("/ui/login?next=") and is_local(location)


def test_a_flash_message_shows_on_the_page(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="rf1-flash")
    _login(client, tenant.slug, users["admin"].email)
    page = client.get(see_other("/ui/users", flash="Saved: a & b, 50%.").headers["location"])
    assert "Saved: a &amp; b, 50%." in page.text
