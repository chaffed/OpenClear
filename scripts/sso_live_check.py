#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Signs in through a real identity provider: Keycloak in a container, set up as a customer's
own (CUSTOMER_ADMIN_PLAN.md, "Testing"). Configures a realm with a confidential OIDC client,
a groups claim, a group and a person, then runs tests/test_web/test_sso_keycloak_live.py,
which connects it as customer-managed connections, OpenID Connect and SAML 2.0, and signs
the person in through Keycloak's own login form each way.

Needs Docker. The container is removed afterwards unless --keep is given.

    python scripts/sso_live_check.py [--keep]
"""

import argparse
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
CONTAINER = "oc-sso-keycloak"
IMAGE = "quay.io/keycloak/keycloak:26.4"
PORT = 18480
BASE = f"http://localhost:{PORT}"
ADMIN_PASSWORD = "Oc-" + secrets.token_hex(8)
REALM = "acme"
CLIENT_ID = "openclear"
CLIENT_SECRET = secrets.token_urlsafe(24)
PERSON_EMAIL = "pat@acme.example.com"
PERSON_PASSWORD = "Pat-" + secrets.token_hex(8)


def run(*args: str, check: bool = True) -> str:
    result = subprocess.run(args, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise SystemExit(f"{' '.join(args)} failed:\n{result.stderr}")
    return result.stdout


def wait_until_up(timeout: float = 240) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{BASE}/realms/master/.well-known/openid-configuration", timeout=5).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(3)
    raise SystemExit("Keycloak didn't start")


def configure() -> None:
    token = httpx.post(f"{BASE}/realms/master/protocol/openid-connect/token", data={
        "grant_type": "password", "client_id": "admin-cli", "username": "admin", "password": ADMIN_PASSWORD}).json()["access_token"]
    api = httpx.Client(base_url=f"{BASE}/admin/realms", headers={"Authorization": f"Bearer {token}"}, timeout=30)

    def ok(resp: httpx.Response) -> httpx.Response:
        if resp.status_code >= 300:
            raise SystemExit(f"Keycloak said {resp.status_code}: {resp.text}")
        return resp

    ok(api.post("", json={"realm": REALM, "enabled": True}))
    ok(api.post(f"/{REALM}/groups", json={"name": "acme-staff"}))
    group_id = next(g["id"] for g in ok(api.get(f"/{REALM}/groups")).json() if g["name"] == "acme-staff")
    ok(api.post(f"/{REALM}/clients", json={
        "clientId": CLIENT_ID, "secret": CLIENT_SECRET, "publicClient": False, "standardFlowEnabled": True,
        "directAccessGrantsEnabled": False, "redirectUris": ["*"], "webOrigins": ["*"],
        "protocolMappers": [{
            "name": "groups", "protocol": "openid-connect", "protocolMapper": "oidc-group-membership-mapper",
            "config": {"claim.name": "groups", "full.path": "false", "id.token.claim": "true", "access.token.claim": "true",
                       "userinfo.token.claim": "true"},
        }],
    }))
    ok(api.post(f"/{REALM}/users", json={
        "username": "pat", "email": PERSON_EMAIL, "emailVerified": True, "enabled": True, "firstName": "Pat", "lastName": "Acme",
        "credentials": [{"type": "password", "value": PERSON_PASSWORD, "temporary": False}],
    }))
    user_id = ok(api.get(f"/{REALM}/users", params={"email": PERSON_EMAIL})).json()[0]["id"]
    ok(api.put(f"/{REALM}/users/{user_id}/groups/{group_id}"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keep", action="store_true", help="leave the Keycloak container running")
    args = parser.parse_args()

    run("docker", "rm", "-fv", CONTAINER, check=False)
    run("docker", "run", "-d", "--name", CONTAINER, "-p", f"{PORT}:8080",
        "-e", "KC_BOOTSTRAP_ADMIN_USERNAME=admin", "-e", f"KC_BOOTSTRAP_ADMIN_PASSWORD={ADMIN_PASSWORD}", IMAGE, "start-dev")
    try:
        print("Waiting for Keycloak…", flush=True)
        wait_until_up()
        configure()
        print("Keycloak is set up; signing in through it.", flush=True)
        env = {**os.environ, "OPENCLEAR_TEST_KEYCLOAK_ISSUER": f"{BASE}/realms/{REALM}", "OPENCLEAR_TEST_KEYCLOAK_CLIENT_ID": CLIENT_ID,
               "OPENCLEAR_TEST_KEYCLOAK_CLIENT_SECRET": CLIENT_SECRET, "OPENCLEAR_TEST_KEYCLOAK_EMAIL": PERSON_EMAIL,
               "OPENCLEAR_TEST_KEYCLOAK_PASSWORD": PERSON_PASSWORD, "OPENCLEAR_TEST_KEYCLOAK_ADMIN_PASSWORD": ADMIN_PASSWORD}
        return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_web/test_sso_keycloak_live.py"],
                              cwd=ROOT, env=env).returncode
    finally:
        if not args.keep:
            run("docker", "rm", "-fv", CONTAINER, check=False)


if __name__ == "__main__":
    sys.exit(main())
