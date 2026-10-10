# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Builds the developer docs site (SDK_PLAN.md P4) from the Markdown already in the repo:
the README, API reference, guides, SDK and integration READMEs, plugin guide and plans. The
pages are copied into one tree, with links between them rewritten to their place in the
site, and built with MkDocs (Material theme) into ./site.

    pip install mkdocs mkdocs-material
    python scripts/build_docs_site.py            # → site/index.html
    python scripts/build_docs_site.py --serve    # live preview"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "https://github.com/chaffed/OpenClear/blob/main/"

# (source in the repo, place in the site, title)
PAGES = [
    ("README.md", "index.md", "Overview"),
    ("docs/guides/certification.md", "getting-started/certification.md", "Integration checklist"),
    ("sandbox/README.md", "getting-started/sandbox.md", "Developer sandbox"),
    ("API.md", "api/reference.md", "API reference"),
    ("sdks/README.md", "sdks/index.md", "SDKs"),
    ("sdks/python/README.md", "sdks/python.md", "Python"),
    ("sdks/typescript/README.md", "sdks/typescript.md", "TypeScript"),
    ("sdks/dotnet/README.md", "sdks/dotnet.md", ".NET"),
    ("sdks/embed/README.md", "sdks/embed.md", "Embedding library"),
    ("docs/guides/embedding.md", "guides/embedding.md", "Embedding in online banking"),
    ("integrations/fineract/README.md", "guides/fineract.md", "Apache Fineract adapter"),
    ("docs/PLUGINS.md", "guides/plugins.md", "Writing a plugin"),
    ("docs/ARCHITECTURE.md", "reference/architecture.md", "Architecture"),
    ("CHANGELOG.md", "reference/changelog.md", "Changelog"),
    ("SDK_PLAN.md", "reference/sdk-plan.md", "SDK plan"),
    ("PLUGIN_PLAN.md", "reference/plugin-plan.md", "Plugin plan"),
]

NAV = """nav:
  - Overview: index.md
  - Getting started:
      - Developer sandbox: getting-started/sandbox.md
      - Integration checklist: getting-started/certification.md
  - API reference: api/reference.md
  - SDKs:
      - sdks/index.md
      - Python: sdks/python.md
      - TypeScript: sdks/typescript.md
      - .NET: sdks/dotnet.md
      - Embedding library: sdks/embed.md
  - Guides:
      - Embedding in online banking: guides/embedding.md
      - Apache Fineract adapter: guides/fineract.md
      - Writing a plugin: guides/plugins.md
  - Reference:
      - Architecture: reference/architecture.md
      - Changelog: reference/changelog.md
      - SDK plan: reference/sdk-plan.md
      - Plugin plan: reference/plugin-plan.md
"""

_LINK = re.compile(r"(\]\()([^)#\s]+)(#[^)\s]*)?(\))")


def _rewrite(text: str, source: Path, placed: Path, sites: dict[Path, Path]) -> str:
    def replace(match: re.Match) -> str:
        target = match.group(2)
        if re.match(r"^[a-z]+:", target) or target.startswith("/"):
            return match.group(0)
        resolved = (source.parent / target).resolve()
        try:
            relative_to_repo = resolved.relative_to(ROOT)
        except ValueError:
            return match.group(0)
        if relative_to_repo in sites:
            new = Path(*[".."] * (len(placed.parent.parts))) / sites[relative_to_repo] if placed.parent.parts else sites[relative_to_repo]
            return f"{match.group(1)}{new.as_posix()}{match.group(3) or ''}{match.group(4)}"
        return f"{match.group(1)}{REPO}{relative_to_repo.as_posix()}{match.group(3) or ''}{match.group(4)}"

    return _LINK.sub(replace, text)


def build(serve: bool = False, out: Path = ROOT / "site") -> int:
    # MkDocs resolves site_dir against its config file, which lives in a temporary directory.
    out = Path(out).resolve()
    sites = {Path(src): Path(dest) for src, dest, _ in PAGES}
    with tempfile.TemporaryDirectory() as tmp:
        docs = Path(tmp) / "docs"
        for src, dest, _title in PAGES:
            target = docs / dest
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_rewrite((ROOT / src).read_text(), ROOT / src, Path(dest), sites))
        screenshots = ROOT / "docs" / "screenshots"
        if screenshots.exists():
            shutil.copytree(screenshots, docs / "docs" / "screenshots")
        config = Path(tmp) / "mkdocs.yml"
        config.write_text(
            "site_name: OpenClear developer docs\n"
            "site_description: Positive pay and ACH exception decisions, open source\n"
            f"repo_url: https://github.com/chaffed/OpenClear\nedit_uri: ''\nsite_dir: {out}\n"
            "theme:\n  name: material\n  features: [navigation.sections, navigation.top, search.suggest, content.code.copy]\n"
            "markdown_extensions: [tables, admonition, toc: {permalink: true}, pymdownx.highlight, pymdownx.superfences]\n"
            "validation:\n  links:\n    not_found: warn\n    anchors: ignore\n"
            + NAV
        )
        command = [sys.executable, "-m", "mkdocs", "serve" if serve else "build", "-f", str(config)]
        if not serve:
            command += ["--clean"]
        return subprocess.call(command)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--out", default=str(ROOT / "site"))
    args = parser.parse_args()
    return build(args.serve, Path(args.out))


if __name__ == "__main__":
    sys.exit(main())
