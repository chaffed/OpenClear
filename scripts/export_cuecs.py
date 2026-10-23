#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Writes the published copies of the CUEC inventory (src/openclear/compliance/cuecs.py;
CONTROLS_PLAN.md CT0) and the OpenClear control inventory (compliance/controls.py, CT1):
- docs/compliance/controls.md, OpenClear's own controls;
- docs/compliance/csf.md, both by NIST CSF 2.0 subcategory (compliance/crosswalks.py, CT6);
- docs/compliance/cuecs.md, the page for people;
- docs/compliance/cuecs.json, for a GRC tool or a script;
- docs/compliance/cuecs.csv, to map onto a control inventory in a spreadsheet: its last
  columns are left blank for the bank's or customer's own control id and owner.

tests/test_compliance/test_cuec_inventory.py fails when they're out of date.

    python scripts/export_cuecs.py
"""

import csv
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "compliance"
sys.path.insert(0, str(ROOT / "src"))

from openclear.compliance import controls, crosswalks, cuecs  # noqa: E402

CSV_COLUMNS = ["id", "party", "nist", "nist_title", "oscal_id", "related", "title", "statement", "complements", "evidence", "check",
               "check_status", "frequency", "csf", "where", "your_control_id", "your_control_owner"]

INTRO = """# Complementary user entity controls

OpenClear's controls rely on the people using it to do their part: a bank, its business
customers, and whoever runs OpenClear. These are those parts, the *complementary user entity
controls* (CUECs). Each is filed under the [{catalog}](https://csrc.nist.gov/pubs/sp/800/53/r5/upd1/final)
control it carries out, so it can be mapped onto a bank's or a customer's own control
inventory. It isn't legal advice ([Compliance](README.md)).

Inventory version {version}. The same list is in [cuecs.json](cuecs.json) and
[cuecs.csv](cuecs.csv); the spreadsheet's last two columns are blank, for your own control id
and owner.

## Reading the inventory

- **Id**: `<party>-<NIST control>-<nn>`. `B` is the bank, `C` the business customer, `H`
  whoever runs OpenClear (the bank, when it hosts OpenClear itself). Ids never change, and a
  withdrawn one is never reused.
- **NIST**: the 800-53 Rev. 5 control or enhancement the CUEC carries out. Exports also give
  it as NIST's OSCAL id (`AC-2(1)` is `ac-2.1`), and list related controls.
- **Evidence**: how OpenClear can show the CUEC operating.
{evidence}
- **CSF 2.0**: the NIST Cybersecurity Framework 2.0 subcategories that NIST's own mapping
  links to the CUEC's 800-53 control ([by subcategory](csf.md)).
- **Check**: the test that produces the evidence. Each is on the Controls page, keeping a
  test history from which an automated CUEC can be attested for a period.
"""


def _csf(keys) -> str:
    return ", ".join(keys) or "—"


def _related(c: cuecs.Cuec) -> str:
    return ", ".join(c.related)


def render_markdown() -> str:
    evidence = "\n".join(f"  - *{name}*: {text}." for name, text in cuecs.EVIDENCE.items())
    parts = [INTRO.format(catalog=cuecs.NIST_CATALOG, version=cuecs.VERSION, evidence=evidence)]
    for party, label in cuecs.PARTIES.items():
        rows = cuecs.for_party(party)
        parts.append(f"## {label}\n")
        parts.append("| Id | NIST | CSF 2.0 | CUEC | What OpenClear does | Evidence | Check | How often | Where |")
        parts.append("|---|---|---|---|---|---|---|---|---|")
        for c in rows:
            nist = f"**{c.nist}** {c.nist_title}" + (f"<br>Related: {_related(c)}" if c.related else "")
            check = f"`{c.check}` ({c.check_status})" if c.check else "—"
            parts.append(f"| {c.id} | {nist} | {_csf(crosswalks.csf_for(c.nist))} | **{c.title}.** {c.statement} | {c.complements} | {c.evidence} | {check} | "
                         f"{c.frequency} | {c.where} |")
        parts.append("")
    if cuecs.PLANNED_CHECKS:
        parts.append("## Checks still to come\n")
        parts.append("| Check | What it shows | CUECs |")
        parts.append("|---|---|---|")
        for key, text in sorted(cuecs.PLANNED_CHECKS.items()):
            ids = ", ".join(c.id for c in cuecs.CUECS if c.check == key)
            parts.append(f"| `{key}` | {text} | {ids} |")
    return "\n".join(parts).rstrip("\n") + "\n"


def _published(c: cuecs.Cuec) -> dict:
    """A CUEC as published: never with a SOC 2 column, which comes from AICPA's mapping that each
    installation reads from its own copy (compliance/crosswalks.py) and isn't redistributed."""
    return {k: v for k, v in c.as_dict().items() if k != "soc2"}


def render_json() -> str:
    data = {"version": cuecs.VERSION, "catalog": cuecs.NIST_CATALOG, "parties": cuecs.PARTIES, "evidence": cuecs.EVIDENCE,
            "planned_checks": cuecs.PLANNED_CHECKS, "cuecs": [_published(c) for c in cuecs.CUECS]}
    return json.dumps(data, indent=2) + "\n"


def render_csv() -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for c in cuecs.CUECS:
        row = c.as_dict()
        writer.writerow({**{k: row[k] for k in CSV_COLUMNS[:-2]}, "related": "; ".join(c.related), "check": c.check or "",
                         "check_status": c.check_status or "",
                         "csf": "; ".join(k["id"] for k in row["csf"]), "your_control_id": "", "your_control_owner": ""})
    return buffer.getvalue()


CONTROLS_INTRO = """# OpenClear's controls

What OpenClear itself does to meet each control objective, filed under the
[{catalog}](https://csrc.nist.gov/pubs/sp/800/53/r5/upd1/final) controls it carries out. Several
rely on the bank, its customers or whoever runs OpenClear to do their part: those are the
[complementary user entity controls](cuecs.md) (CUECs). It isn't legal advice
([Compliance](README.md)).

Inventory version {version}. The same list, with each check's live status for your bank, is at
`GET /api/v1/compliance/controls`, and on the Controls page.

## Reading the inventory

- **Id**: `OC-<family>-<nn>`. Ids never change, and a withdrawn one is never reused.
- **NIST**: the 800-53 Rev. 5 controls it carries out, the main one first.
- **CSF 2.0**: the NIST Cybersecurity Framework 2.0 subcategories that NIST's own mapping
  links to those 800-53 controls ([by subcategory](csf.md)).
- **Type**: {types}.
- **Nature**: {natures}. A configurable control relies on the CUEC that sets it.
- **Checks**: the tests of the control, each on the Controls page.
"""


def render_controls_markdown() -> str:
    types = "; ".join(f"*{name}* {text}" for name, text in controls.TYPES.items())
    natures = "; ".join(f"*{name.replace('_', ' ')}*, {text}" for name, text in controls.NATURES.items())
    parts = [CONTROLS_INTRO.format(catalog=cuecs.NIST_CATALOG, version=controls.VERSION, types=types, natures=natures)]
    for family, label in controls.FAMILIES.items():
        parts.append(f"## {label}\n")
        parts.append("| Id | Control | NIST | CSF 2.0 | Type | Checks | Relies on | Where |")
        parts.append("|---|---|---|---|---|---|---|---|")
        for c in controls.CONTROLS:
            if c.family != family or c.withdrawn:
                continue
            nist = "<br>".join(f"**{n}** {cuecs.NIST_TITLES[n]}" for n in c.nist)
            checks = "<br>".join(f"`{k}` ({c.check_status(k)})" for k in c.checks) or "—"
            relies = ", ".join(c.cuecs) or "—"
            parts.append(f"| {c.id} | **{c.title}.** {c.statement} | {nist} | {_csf(crosswalks.csf_for_all(c.nist))} | {c.type}, {c.nature.replace('_', ' ')} | {checks} | "
                         f"{relies} | {c.where} |")
        parts.append("")
    return "\n".join(parts) + "\n"


def _as_of(page: str) -> str:
    """The date a regulatory page was last checked against its sources (compliance/regulatory.py)."""
    from openclear.compliance import regulatory

    return next(p.current_as_of.isoformat() for p in regulatory.PAGES if p.path == page)


CSF_INTRO = """# NIST CSF 2.0

*Current as of {as_of}. Checked against its sources at least once a year (control check `regulatory_currency`).*

OpenClear's [controls](controls.md) and the [complementary user entity controls](cuecs.md)
(CUECs) by NIST Cybersecurity Framework 2.0 subcategory. Both inventories are filed under
{catalog} controls. This page follows NIST's own mapping from CSF 2.0 to those
controls (the informative references in the [CSF 2.0 Reference Tool]({url})), not a judgment
of ours. It isn't legal advice ([Compliance](README.md)).

A subcategory that NIST maps to a control also covers its enhancements (`AC-2` covers
`AC-2(1)`), and one mapped to a whole family (`IR`) covers every control in it. A subcategory
with nothing below isn't one OpenClear's controls or CUECs address.

{uncovered}
"""


def render_csf_markdown() -> str:
    live = [c for c in controls.CONTROLS if not c.withdrawn]
    current = [c for c in cuecs.CUECS if not c.withdrawn]
    unmapped = sorted({n for c in live for n in c.nist} | {c.nist for c in current}, key=list(cuecs.NIST_TITLES).index)
    unmapped = [n for n in unmapped if not crosswalks.csf_for(n)]
    uncovered = ("NIST's mapping links no subcategory to " + ", ".join(f"`{n}`" for n in unmapped) +
                 ", so a control or CUEC filed only under those isn't on this page.") if unmapped else ""
    parts = [CSF_INTRO.format(catalog=cuecs.NIST_CATALOG, url=crosswalks.CSF_SOURCE_URL, uncovered=uncovered, as_of=_as_of("csf.md")).rstrip() + "\n"]
    for function, label in crosswalks.CSF_FUNCTIONS.items():
        parts.append(f"## {label} ({function})\n")
        parts.append("| Subcategory | OpenClear controls | CUECs |")
        parts.append("|---|---|---|")
        for key in crosswalks.SUBCATEGORIES:
            if not key.startswith(function + "."):
                continue
            ours = [c.id for c in live if key in crosswalks.csf_for_all(c.nist)]
            theirs = [c.id for c in current if key in crosswalks.csf_for(c.nist)]
            parts.append(f"| **{key}** {crosswalks.csf_title(key)} | {', '.join(ours) or '—'} | {', '.join(theirs) or '—'} |")
        parts.append("")
    return "\n".join(parts)


OUTPUTS = {"controls.md": render_controls_markdown, "csf.md": render_csf_markdown, "cuecs.md": render_markdown, "cuecs.json": render_json, "cuecs.csv": render_csv}


def main() -> None:
    for name, render in OUTPUTS.items():
        (OUT / name).write_text(render(), encoding="utf-8")
        print(f"Wrote {OUT / name}")


if __name__ == "__main__":
    main()
