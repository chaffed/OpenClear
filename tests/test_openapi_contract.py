# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The published API contract (SDK_PLAN.md P0): openapi/openclear-v1.json must match what
the code generates. A breaking change fails with what broke; an additive one asks for the
file to be regenerated (python scripts/export_openapi.py). /api/v1 only changes
additively; a breaking change needs a new version, called out in the changelog."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMMITTED = ROOT / "openapi" / "openclear-v1.json"
_spec = importlib.util.spec_from_file_location("export_openapi", ROOT / "scripts" / "export_openapi.py")
export_openapi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(export_openapi)

_METHODS = {"get", "put", "post", "patch", "delete"}


def _resolve(spec: dict, schema: dict | None) -> dict:
    seen = 0
    while schema and "$ref" in schema and seen < 20:
        schema = spec["components"]["schemas"][schema["$ref"].split("/")[-1]]
        seen += 1
    return schema or {}


def _body_schema(spec: dict, op: dict) -> dict:
    content = (op.get("requestBody") or {}).get("content", {})
    media = content.get("application/json") or content.get("application/x-www-form-urlencoded") or next(iter(content.values()), {})
    return _resolve(spec, media.get("schema"))


def _response_schema(spec: dict, op: dict) -> dict:
    for code in ("200", "201"):
        media = (op.get("responses", {}).get(code, {}).get("content") or {}).get("application/json")
        if media:
            schema = _resolve(spec, media.get("schema"))
            if schema.get("type") == "array":
                schema = _resolve(spec, schema.get("items"))
            return schema
    return {}


def breaking_changes(old: dict, new: dict) -> list[str]:
    """What a client written against `old` would find broken in `new`."""
    problems = []
    for path, old_ops in old["paths"].items():
        new_ops = new["paths"].get(path)
        for method in _METHODS & set(old_ops):
            where = f"{method.upper()} {path}"
            if not new_ops or method not in new_ops:
                problems.append(f"{where} was removed")
                continue
            old_op, new_op = old_ops[method], new_ops[method]
            old_params = {(p["in"], p["name"]): p for p in old_op.get("parameters", [])}
            for key, param in {(p["in"], p["name"]): p for p in new_op.get("parameters", [])}.items():
                if param.get("required") and not old_params.get(key, {}).get("required"):
                    problems.append(f"{where}: {key[0]} parameter {key[1]!r} is now required")
            old_body, new_body = _body_schema(old, old_op), _body_schema(new, new_op)
            for field in set(new_body.get("required", [])) - set(old_body.get("required", [])):
                problems.append(f"{where}: request field {field!r} is now required")
            old_out, new_out = _response_schema(old, old_op), _response_schema(new, new_op)
            for field in set(old_out.get("properties", {})) - set(new_out.get("properties", {})):
                problems.append(f"{where}: response field {field!r} was removed")
    return problems


def shape(spec: dict) -> dict:
    """The parts of the contract a client depends on: endpoints, parameters, and request
    and response fields. Compared instead of the file's text, so a library update that only
    changes how the same API is written out doesn't fail the build."""
    result = {}
    for path, ops in spec["paths"].items():
        for method in _METHODS & set(ops):
            op = ops[method]
            body, out = _body_schema(spec, op), _response_schema(spec, op)
            result[f"{method.upper()} {path}"] = {
                "parameters": sorted((p["in"], p["name"], bool(p.get("required"))) for p in op.get("parameters", [])),
                "body": sorted(body.get("properties", {})), "body_required": sorted(body.get("required", [])),
                "response": sorted(out.get("properties", {})),
            }
    return result


def test_the_committed_contract_matches_the_code():
    committed = json.loads(COMMITTED.read_text())
    current = export_openapi.current_spec()
    broken = breaking_changes(committed, current)
    assert not broken, "Breaking API changes (keep /api/v1 additive):\n  " + "\n  ".join(broken)
    assert shape(current) == shape(committed), (
        "The API changed (additively). Regenerate the contract: python scripts/export_openapi.py"
    )


def test_the_breaking_change_check_catches_what_it_should():
    committed = json.loads(COMMITTED.read_text())
    mutated = json.loads(COMMITTED.read_text())
    del mutated["paths"]["/api/v1/decisions"]
    mutated["paths"]["/api/v1/decisions/consumers"]["get"].setdefault("parameters", []).append(
        {"in": "query", "name": "region", "required": True, "schema": {"type": "string"}}
    )
    issued = mutated["components"]["schemas"]["IssuedItemRead"]
    issued["properties"].pop("check_number")
    broken = breaking_changes(committed, mutated)
    assert any("GET /api/v1/decisions was removed" in b for b in broken)
    assert any("'region' is now required" in b for b in broken)
    assert any("'check_number' was removed" in b for b in broken)
    assert breaking_changes(committed, committed) == []
