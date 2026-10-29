# Third-party components: inventory and notices

A bank's due diligence, and the licenses of what OpenClear ships, both ask for a list of the
third-party components in it. Each release's CycloneDX SBOM (`scripts/sbom.py`) lists the Python
packages in the image and the two vendored JavaScript files. What's missing:
- a page a person can read;
- the license notices that MIT, BSD and Apache-2.0 components ask to travel with the software;
- the icons compiled into the sprite;
- the image's operating-system packages;
- the SDKs' own dependencies;
- the components used only for tests and development.

## Decisions (the product owner, 2026-10-23: "go with your recommendation")

- **TP-D1. One source, two outputs.** The page and the notices are generated from the same
  sources as the SBOM (the hashed locks and the vendored-assets list), so they can't drift. A test
  fails when the committed page is stale, as for the other generated pages.
- **TP-D2. A readable inventory, `docs/compliance/third-party.md`:** each component's name,
  version, license and where it's used, grouped as:
  - in the image (Python);
  - vendored into the web pages (JavaScript, icons);
  - in the image's operating system;
  - in the SDKs;
  - used only for development and tests (not shipped).
- **TP-D3. Notices.** `THIRD_PARTY_NOTICES.md` carries each shipped component's copyright and
  license text, from the installed package's own license file where it has one. The in-app
  License page links to it, and it's in the image beside `LICENSE`.
- **TP-D4. Vendored assets, not only JavaScript.** `requirements/vendored-js.json` becomes
  `requirements/vendored.json` and gains Material Symbols (Apache-2.0, in
  `static/icons/sprite.svg`), checked by the existing SBOM test (version, file hash).
- **TP-D5. The image's own SBOM.** The release workflow also scans the built image (Syft) and
  attaches `openclear-<version>-image.cdx.json`, covering the base image and the system packages
  (Tesseract, WeasyPrint's libraries, libpq). The Python SBOM stays as it is.
- **TP-D6. The SDKs.** Each SDK's dependencies are listed on the page from its own manifest
  (`package.json`, `.csproj`, `pyproject.toml`); each SDK's release carries its own SBOM later, in
  its own repository.

## Stages

| Stage | What | Status |
|---|---|---|
| TP1 | `vendored.json` with the icons; the generator for `third-party.md` and `THIRD_PARTY_NOTICES.md`; the staleness test | Done |
| TP2 | The License page's link; the notices file in the image | Done |
| TP3 | The image SBOM in the release workflow | Done |
| TP4 | Docs: README, `tprm.md`, `security-metrics.md`, the compliance index, CHANGELOG | |

## Progress log

- 2026-10-23: Plan written with the product owner's decisions TP-D1 to TP-D6.
- 2026-10-26: **Follow-up:** mkdocs-material 9.8 announces its end of life on 5 May 2027, with
  Zensical, by the same team, as its successor. The docs site (`scripts/build_docs_site.py`) moves
  to Zensical before then; until then the build sets `NO_MKDOCS_2_WARNING=1`, and its test fails
  only on MkDocs' own warning lines, not on a banner that mentions the word.
- 2026-10-29: TP1 built. `requirements/vendored-js.json` became `requirements/vendored.json`: the
  vendored files (now with the icon sprite, versioned by the day it was fetched), the image's
  system packages (the test checks the Dockerfile's final stage names exactly these), the .NET
  SDK's NuGet licenses, and license texts for wheels that leave theirs out (dnspython 2.9.0).
  `scripts/third_party.py` writes `docs/compliance/third-party.md` and `THIRD_PARTY_NOTICES.md`
  (104 Python packages in the image, 3 vendored files, 7 system entries, the SDKs, 80 for
  development only). It reads licenses from installed packages and refuses to run unless the
  image's packages are installed at their locked versions, so it runs in `python:3.13-slim` with
  both locks (its docstring has the command). The tests check names and versions against the
  locks anywhere, and the exact output where the locks are installed.
  **Follow-up for TP3:** a lock bump (Dependabot, the weekly refresh) now fails the staleness test
  until the files are regenerated; the lock-refresh workflow should regenerate them in the same
  container.
- 2026-10-29: TP2 built. The License page links to `/ui/license/third-party`, which shows
  `THIRD_PARTY_NOTICES.md` (public, like the License page; a missing file points to the source
  instead of failing), and the image copies the notices beside `LICENSE`.
- 2026-10-29: The ZAP baseline flagged "delete from the Original Code" (MPL 1.1, on the notices
  page) as SQL disclosure. `zap-rules.tsv` waivers may now name the URLs they cover; this one
  covers only `/ui/license/third-party`.
- 2026-10-29: TP3 built. The release workflow's `image-sbom` job builds the image from the tag and
  attaches `openclear-<version>-image.cdx.json` from Syft (the CI image's pin): packages only, no
  per-file entries (282 components, about 780 KB, against 3,816 with them). Tried locally on
  the image: 161 Debian packages, Tesseract and WeasyPrint's libraries among them, and the 107
  Python packages. `scripts/third_party.sh` runs the generator in the image's base with both
  locks; the weekly lock refresh runs it, and CLAUDE.md says to run it after any lock change.
  Dependabot's PRs still need it run by hand: giving a workflow write access on them isn't worth it.
  The image is now 2.4 GB (it was 450 MB at 1.12), mostly the scientific and cloud packages; worth
  a look separately.
