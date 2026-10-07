"""
tests/test_schema_drift.py — Assert that mcp_tools.json and server._TOOLS agree.

Catches drift between the documentation JSON and the live server schema.
Checks: tool names, required parameter names, and all property names.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Add src/ so we can import the server module
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from woocommerce_connector.server import _TOOLS  # noqa: E402


def _load_mcp_tools_json() -> list[dict]:
    p = ROOT / "mcp_tools.json"
    data = json.loads(p.read_text())
    # The JSON has a top-level "tools" array
    return data["tools"]


def _server_tool_names() -> set[str]:
    return {t.name for t in _TOOLS}


def _json_tool_names() -> set[str]:
    return {t["name"] for t in _load_mcp_tools_json()}


def _server_schema(name: str) -> dict:
    for t in _TOOLS:
        if t.name == name:
            schema = t.input_schema
            # input_schema is a Pydantic model; convert to dict
            if hasattr(schema, "model_dump"):
                return schema.model_dump(exclude_none=True)
            return dict(schema) if schema else {}
    raise KeyError(name)


def _json_schema(name: str) -> dict:
    for t in _load_mcp_tools_json():
        if t["name"] == name:
            return t.get("input_schema", {})
    raise KeyError(name)


class TestSchemaSync:

    def test_same_tool_names(self):
        """mcp_tools.json and server._TOOLS must list exactly the same tool names."""
        server_names = _server_tool_names()
        json_names = _json_tool_names()

        only_in_server = server_names - json_names
        only_in_json = json_names - server_names

        errors = []
        if only_in_server:
            errors.append(f"In server but NOT in mcp_tools.json: {sorted(only_in_server)}")
        if only_in_json:
            errors.append(f"In mcp_tools.json but NOT in server: {sorted(only_in_json)}")

        assert not errors, "\n".join(errors)

    def test_required_params_match(self):
        """Required parameters must be identical in both representations."""
        common = _server_tool_names() & _json_tool_names()
        errors = []

        for name in sorted(common):
            s_schema = _server_schema(name)
            j_schema = _json_schema(name)

            s_required = set(s_schema.get("required", []))
            j_required = set(j_schema.get("required", []))

            if s_required != j_required:
                errors.append(
                    f"Tool '{name}': required params differ.\n"
                    f"  server:       {sorted(s_required)}\n"
                    f"  mcp_tools.json: {sorted(j_required)}"
                )

        assert not errors, "\n".join(errors)

    def test_property_names_match(self):
        """Property names declared in inputSchema must match in both representations."""
        common = _server_tool_names() & _json_tool_names()
        errors = []

        for name in sorted(common):
            s_schema = _server_schema(name)
            j_schema = _json_schema(name)

            s_props = set(s_schema.get("properties", {}).keys())
            j_props = set(j_schema.get("properties", {}).keys())

            if s_props != j_props:
                errors.append(
                    f"Tool '{name}': property names differ.\n"
                    f"  server:         {sorted(s_props)}\n"
                    f"  mcp_tools.json: {sorted(j_props)}"
                )

        assert not errors, "\n".join(errors)
