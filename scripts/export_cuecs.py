#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Writes the published copies of the CUEC inventory (src/openclear/compliance/cuecs.py;
CONTROLS_PLAN.md CT0):
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

from openclear.compliance import cuecs  # noqa: E402

CSV_COLUMNS = ["id", "party", "nist", "nist_title", "oscal_id", "related", "title", "statement", "complements", "evidence", "check",
               "check_status", "frequency", "where", "your_control_id", "your_control_owner"]

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
- **Check**: the test that produces the evidence. *Live* checks are on the Controls page
  today; *planned* ones arrive with CONTROLS_PLAN.md CT2 and CT3, after which an automated CUEC can be
  attested from OpenClear's test history for a period.
"""


def _related(c: cuecs.Cuec) -> str:
    return ", ".join(c.related)


def render_markdown() -> str:
    evidence = "\n".join(f"  - *{name}*: {text}." for name, text in cuecs.EVIDENCE.items())
    parts = [INTRO.format(catalog=cuecs.NIST_CATALOG, version=cuecs.VERSION, evidence=evidence)]
    for party, label in cuecs.PARTIES.items():
        rows = cuecs.for_party(party)
        parts.append(f"## {label}\n")
        parts.append("| Id | NIST | CUEC | What OpenClear does | Evidence | Check | How often | Where |")
        parts.append("|---|---|---|---|---|---|---|---|")
        for c in rows:
            nist = f"**{c.nist}** {c.nist_title}" + (f"<br>Related: {_related(c)}" if c.related else "")
            check = f"`{c.check}` ({c.check_status})" if c.check else "—"
            parts.append(f"| {c.id} | {nist} | **{c.title}.** {c.statement} | {c.complements} | {c.evidence} | {check} | "
                         f"{c.frequency} | {c.where} |")
        parts.append("")
    parts.append("## Checks still to come\n")
    parts.append("| Check | What it shows | CUECs |")
    parts.append("|---|---|---|")
    for key, text in sorted(cuecs.PLANNED_CHECKS.items()):
        ids = ", ".join(c.id for c in cuecs.CUECS if c.check == key)
        parts.append(f"| `{key}` | {text} | {ids} |")
    return "\n".join(parts) + "\n"


def render_json() -> str:
    data = {"version": cuecs.VERSION, "catalog": cuecs.NIST_CATALOG, "parties": cuecs.PARTIES, "evidence": cuecs.EVIDENCE,
            "planned_checks": cuecs.PLANNED_CHECKS, "cuecs": [c.as_dict() for c in cuecs.CUECS]}
    return json.dumps(data, indent=2) + "\n"


def render_csv() -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for c in cuecs.CUECS:
        row = c.as_dict()
        writer.writerow({**{k: row[k] for k in CSV_COLUMNS[:-2]}, "related": "; ".join(c.related), "check": c.check or "",
                         "check_status": c.check_status or "", "your_control_id": "", "your_control_owner": ""})
    return buffer.getvalue()


OUTPUTS = {"cuecs.md": render_markdown, "cuecs.json": render_json, "cuecs.csv": render_csv}


def main() -> None:
    for name, render in OUTPUTS.items():
        (OUT / name).write_text(render(), encoding="utf-8")
        print(f"Wrote {OUT / name}")


if __name__ == "__main__":
    main()
