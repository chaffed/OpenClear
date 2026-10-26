# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The developer docs site builds, every page is there, and no link between them is broken
(scripts/build_docs_site.py). Skipped without MkDocs (pip install -e '.[docs]')."""

import importlib.util
import re
from pathlib import Path

import pytest

pytest.importorskip("mkdocs")
pytest.importorskip("material")

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("build_docs_site", ROOT / "scripts" / "build_docs_site.py")
build_docs_site = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_docs_site)


def test_the_docs_site_builds_with_every_page(tmp_path, capfd):
    assert build_docs_site.build(out=tmp_path / "site") == 0
    output = capfd.readouterr()
    # MkDocs' own warnings are log lines ("WARNING  -  ..."), not the word anywhere: a package's
    # banner that mentions the word isn't a broken build.
    warnings = [line for line in (output.out + output.err).splitlines() if re.match(r"\s*WARNING\s+-", line)]
    assert not warnings, "\n".join(warnings)[-3000:]
    for _src, dest, _title in build_docs_site.PAGES:
        page = tmp_path / "site" / (dest[:-3] + "/index.html" if not dest.endswith("index.md") else dest[:-3] + ".html")
        assert page.exists(), dest
