# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Maps each released version to the Alembic revision that was `head` at release time.
Add one new entry here as part of cutting each release whose head revision actually
changed (a patch that adds no migration doesn't need a new entry) -- never edit or remove
a past entry, they're the record tests/test_migrations/test_upgrade_downgrade_policy.py
checks the downgrade-two-minor-versions policy against.

See README.md's "Upgrade and downgrade support" section for the full policy this backs:
upgrading is always supported, from any prior version; downgrading is only supported up
to 2 minor versions back, and never across a major version boundary.
"""

VERSION_HISTORY: dict[str, str] = {
    "1.0.0": "b2c3d4e5f6a7",  # head as of this policy's introduction
    "1.5.0": "c3e8f1a5d9b2",
    "1.6.0": "f7c9e1a3b5d6",
    "1.7.0": "b9d1f3a5c7e8",
    "1.8.0": "e6a8c0e2a4c7",
    "1.9.0": "a8c0e2a4c6e9",
    "1.10.0": "c0e2a4c6e8f1",
    "1.11.0": "e2a4c6e8a0b3",
    "1.12.0": "f3b5d7f9b1c4",
    "1.13.0": "a4c6e8a0c2d5",
    "1.14.0": "b5d7f9b1d3e6",
    "1.15.0": "c6e8a0c2e4f7",
    "1.16.0": "d7f9b1c3e5a8",
    "1.17.0": "f1a3c5e7b9d2",
    "1.18.0": "c4e6a8b0d2f3",
    "1.19.0": "d5f7a9b1c3e4",
    "1.20.0": "e6a8b0c2d4f5",
    "1.21.0": "d3f5b7c9e1a3",
    "1.21.1": "e4a6c8d0f2b5",
    "1.21.2": "e4a6c8d0f2b5",
    "1.22.0": "a1c3e5f7b9d2",
    "1.22.1": "a1c3e5f7b9d2",
    "1.23.0": "b2d4f6a8c0e1",
    "1.24.0": "c3e5a7b9d1f2",
    "1.25.0": "d4f6b8c0e2a3",
    "1.25.1": "d4f6b8c0e2a3",
    "1.26.0": "f6b8d0a2c4e5",
    "1.27.0": "5b8e2d4f6a1c",
    "1.28.0": "4c47d3f3a975",
    "1.28.1": "4c47d3f3a975",
    "1.29.0": "d867a5f28e73",
    "1.30.0": "b3f1c7a9e2d5",
}


def _parse(version: str) -> tuple[int, int, int]:
    major, minor, patch = version.split(".")
    return int(major), int(minor), int(patch)


def minor_versions_back(version_history: dict[str, str], current: str, count: int) -> str | None:
    """The revision `count` minor versions before `current`'s, within the same major
    version only -- crossing a major-version boundary is never required by policy, so
    this returns None rather than reaching across one. Also None if there simply isn't
    `count` minor versions of recorded history yet (never an error -- callers should
    treat this as "not enough history to test yet", not a failure)."""
    current_major, current_minor, _ = _parse(current)

    # One entry per distinct (major, minor), keeping whichever recorded patch is highest
    # for it -- a patch release that adds no new migration/minor bump isn't its own step
    # "back".
    by_minor: dict[tuple[int, int], tuple[int, str]] = {}
    for version, revision in version_history.items():
        major, minor, patch = _parse(version)
        key = (major, minor)
        if key not in by_minor or patch > by_minor[key][0]:
            by_minor[key] = (patch, revision)

    candidates = sorted(
        (key for key in by_minor if key[0] == current_major and key <= (current_major, current_minor)),
        reverse=True,
    )
    if len(candidates) <= count:
        return None
    return by_minor[candidates[count]][1]
