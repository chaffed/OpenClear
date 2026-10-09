# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The SDKs' generated code matches the published API contract (SDK_PLAN.md P1). If this
fails after an API change: python scripts/export_openapi.py && python scripts/generate_sdks.py"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("generate_sdks", ROOT / "scripts" / "generate_sdks.py")
generate_sdks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(generate_sdks)


def test_generated_sdk_code_is_current():
    stale = [str(path.relative_to(ROOT)) for path, text in generate_sdks.generate().items() if not path.exists() or path.read_text() != text]
    assert not stale, "Regenerate the SDKs (python scripts/generate_sdks.py): " + ", ".join(stale)


def test_every_api_endpoint_is_in_the_sdks():
    import json

    spec = json.loads((ROOT / "openapi" / "openclear-v1.json").read_text())
    operations = generate_sdks.read_operations(spec)
    python = (ROOT / "sdks/python/src/openclear_sdk/_generated.py").read_text()
    typescript = (ROOT / "sdks/typescript/src/generated.ts").read_text()
    for op in operations:
        assert f"def {generate_sdks.py_ident(op.name)}(" in python, op.name
        assert f"  {generate_sdks.camel(op.name)}(" in typescript, op.name
