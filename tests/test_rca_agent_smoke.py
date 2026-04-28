"""Smoke tests for the RCA agent loop. Mocks the Anthropic client so we can
exercise the loop without burning API tokens or needing creds."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from apps.api.agents.rca import (
    MAX_SUBAGENTS,
    MAX_TOOL_CALLS,
    SUBAGENT_MAX_CALLS,
    _MCP_DISPATCH,
    _tool_definitions,
)


def test_tool_definitions_complete(monkeypatch):
    """When both Datadog keys are set, all MCP tools are advertised."""
    monkeypatch.setenv("DATADOG_API_KEY", "fake")
    monkeypatch.setenv("DATADOG_APP_KEY", "fake")
    import apps.api.settings as settings_mod
    settings_mod._settings = None

    names = {t["name"] for t in _tool_definitions()}
    for k in _MCP_DISPATCH:
        assert k in names, f"missing tool definition for MCP tool: {k}"
    assert "code_exec" in names
    assert "write_final_rca" in names
    assert "Task" in names


def test_datadog_tools_hidden_when_keys_missing(monkeypatch):
    """Without Datadog keys, the agent should NOT see datadog tools — saves
    tokens and prevents wasted tool calls trying a path that can't authenticate."""
    monkeypatch.setenv("DATADOG_API_KEY", "")
    monkeypatch.setenv("DATADOG_APP_KEY", "")
    import apps.api.settings as settings_mod
    settings_mod._settings = None

    names = {t["name"] for t in _tool_definitions()}
    assert "trk_datadog_search" not in names
    assert "trk_datadog_trace" not in names
    # All non-datadog tools still present
    assert "trk_kubectl_logs" in names
    assert "trk_mssql_query" in names


def test_datadog_tools_hidden_when_only_one_key_present(monkeypatch):
    """Both API key and App key are required — only one is not enough."""
    monkeypatch.setenv("DATADOG_API_KEY", "fake")
    monkeypatch.setenv("DATADOG_APP_KEY", "")
    import apps.api.settings as settings_mod
    settings_mod._settings = None

    names = {t["name"] for t in _tool_definitions()}
    assert "trk_datadog_search" not in names
    assert "trk_datadog_trace" not in names


def test_caps_are_sane():
    assert 8 <= MAX_TOOL_CALLS <= 16
    assert 2 <= SUBAGENT_MAX_CALLS <= 6
    assert 1 <= MAX_SUBAGENTS <= 5


def test_tool_definitions_have_input_schemas():
    for t in _tool_definitions():
        assert "input_schema" in t
        assert t["input_schema"].get("type") == "object"


def test_run_rca_blocks_when_no_anthropic_key(tmp_path, monkeypatch):
    """Without a key, the loop should yield a status event and stop — not crash.

    We forcibly stub get_settings so this is robust even if the developer's
    .env (or process env) has a real key populated.
    """
    class _FakeSettings:
        anthropic_api_key = ""
        openai_api_key = ""
        datadog_api_key = ""
        datadog_app_key = ""
        sherlock_investigations_dir = tmp_path
        sherlock_db_path = tmp_path / "fake.db"
        sherlock_demo_mode = False
        sherlock_release = "ppe"

    monkeypatch.setattr("apps.api.agents.rca.get_settings", lambda: _FakeSettings())

    from apps.api.agents.rca import run_rca

    async def collect():
        events = []
        async for e in run_rca("device AABB events not in lookup_parcels", entities={"tape_id": "AABBCCDDEEFF"}):
            events.append(e)
        return events

    events = asyncio.run(collect())
    assert any("ANTHROPIC_API_KEY not set" in e for e in events)
    assert any("done" in e for e in events)
