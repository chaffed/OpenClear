#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Regenerates src/openclear/static/icons/sprite.svg from Google's Material Symbols
(Outlined) icon set.

Fetches each icon's single-path SVG from the official material-design-icons repo (raw,
byte-exact content -- never routed through anything that could rewrite the path data) and
combines them into one <symbol>-per-icon sprite, referenced throughout the app via
templates/_macros/icon.html's `icon(name)` macro and a plain <use href="...#icon-{name}">.

The committed sprite is a static asset, same posture as docs/screenshots/ (see
scripts/generate_screenshots.py) -- this is a manual, occasional dev task, not something
CI runs. Rerun it after adding a new icon name to ICONS below, then commit the result.

Usage:
    python scripts/fetch_material_icons.py
"""

import re
import sys
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPRITE_PATH = PROJECT_ROOT / "src" / "openclear" / "static" / "icons" / "sprite.svg"

ICON_URL = "https://raw.githubusercontent.com/google/material-design-icons/master/symbols/web/{name}/materialsymbolsoutlined/{name}_24px.svg"

# name -> where it's used, so this list stays legible as it grows. Every entry here must
# be a real Material Symbols (Outlined) icon name -- a typo fails loudly below rather than
# silently producing a missing icon at runtime.
ICONS: dict[str, str] = {
    # Left nav
    "menu": "nav: mobile hamburger toggle (templates/base.html .nav-toggle)",
    "account_balance": "nav: Accounts",
    "receipt_long": "nav: Issued Items",
    "block": "nav: Stop Payments; button: Revoke",
    "paid": "nav: Paid Items",
    "request_quote": "nav: Billing",
    "image": "nav: Check Images",
    "fact_check": "nav: ACH Authorizations",
    "sync_alt": "nav: ACH Transactions",
    "assignment_return": "nav: ACH Return Reasons",
    "home": "nav: Home",
    "flag": "nav: Exceptions",
    "gavel": "nav: Unauthorized Debit Statement",
    "model_training": "nav: Fraud Training Data",
    "groups": "nav: Customers",
    "person": "nav: Users",
    "admin_panel_settings": "nav: Security Groups",
    "checklist": "nav: Getting Started; button: Getting Started checklist, Open checklist",
    "settings": "nav: Settings",
    "history": "nav: Audit Log",
    "tune": "nav: Admin; button: Manage",
    "menu_book": "nav: End User Documentation",
    "library_books": "nav: Admin Documentation",
    "swap_horiz": "nav: Switch organization; button: tenant-switch row",
    "vpn_key": "nav: Security; button: Use/Register security key, Security keys link",
    # Buttons -- reused labels
    "save": "button: Save",
    "upload": "button: Upload, Upload zip, Upload X9.37 file",
    "upload_file": "button: Bulk upload",
    "add": "button: New *, Create, Add user",
    "person_add": "button: Grant access, Look up / grant multi-customer access",
    "send": "button: Submit, Submit recommendation, Submit decision",
    "add_link": "button: Add connection, Add mapping",
    "download": "button: Start export, Export CSV/JSON, Download original file",
    "arrow_back": "button: Back to customer",
    "refresh": "button: Retrain, Reprocess",
    "check_circle": "button: Activate",
    # Buttons -- one-off
    "cancel": "button: Void",
    "close": "button: Cancel (stop payment)",
    "verified": "button: Verify chain",
    "restart_alt": "button: Reset demo data now",
    "delete": "button: Remove (webauthn)",
    "undo": "button: Back out this upload, Retract",
    "search": "button: Look up",
    "draw": "button: Sign statement",
    "login": "button: Log in, Sign in with {provider}",
    "visibility": "button: View",
    "edit": "button: Edit",
    "notifications": "button: Notification preferences link",
    "zoom_in": "docs: mermaid diagram zoom-in control",
    "zoom_out": "docs: mermaid diagram zoom-out control",
    "campaign": "nav: Banner Message (customer-scoped self-service)",
    "format_bold": "markdown editor: Bold toolbar button",
    "format_italic": "markdown editor: Italic toolbar button",
    "link": "markdown editor: Insert link toolbar button",
    "format_list_bulleted": "markdown editor: Bulleted list toolbar button",
    # Control panel tiles (templates/settings/panel.html; web/control_panel.py)
    "palette": "tile: Branding",
    "contact_support": "tile: Contact info",
    "password": "tile: Password policy",
    "timer": "tile: Timeouts",
    "key": "tile: Single sign-on",
    "rule": "tile: Segregation of duties",
    "how_to_reg": "tile: Dual control",
    "notifications_active": "tile: Reminders",
    "analytics": "tile: Fraud scoring model",
    "description": "tile: Decision files",
    "web": "tile: Embedding",
    "security": "tile: Security event forwarding",
    "drive_folder_upload": "tile: Auto-import",
    "science": "tile: Model risk",
    "extension": "tile: Plugins",
    "webhook": "tile: Webhooks",
    "verified_user": "tile: Controls",
    "more_horiz": "responsive_table: a row's action menu on phones (_macros/responsive_table.html)",
}


def _fetch_icon_svg(name: str) -> tuple[str, str]:
    url = ICON_URL.format(name=name)
    with urllib.request.urlopen(url, timeout=15) as resp:
        if resp.status != 200:
            raise RuntimeError(f"{name}: HTTP {resp.status} fetching {url}")
        raw = resp.read().decode("utf-8")

    view_box_match = re.search(r'viewBox="([^"]+)"', raw)
    paths = re.findall(r"<path[^>]*/>", raw)
    if not view_box_match or not paths:
        raise RuntimeError(f"{name}: couldn't parse viewBox/path out of fetched SVG:\n{raw}")
    return view_box_match.group(1), "".join(paths)


def build_sprite() -> str:
    symbols = []
    for name in sorted(ICONS):
        view_box, paths = _fetch_icon_svg(name)
        symbols.append(f'  <symbol id="icon-{name}" viewBox="{view_box}">{paths}</symbol>')
        print(f"  fetched {name}")
    body = "\n".join(symbols)
    return f'<svg xmlns="http://www.w3.org/2000/svg" style="display:none" aria-hidden="true">\n{body}\n</svg>\n'


def main() -> None:
    print(f"Fetching {len(ICONS)} Material Symbols (Outlined) icons...")
    sprite = build_sprite()
    SPRITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SPRITE_PATH.write_text(sprite)
    print(f"Wrote {SPRITE_PATH} ({len(ICONS)} icons)")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 -- this is a dev script; any failure should just print and exit non-zero
        print(f"FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
