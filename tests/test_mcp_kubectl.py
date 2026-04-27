"""Smoke tests for trk-kubectl MCP server. Tests the in-process call_tool path
with kubectl mocked via subprocess.run."""
from __future__ import annotations

import asyncio
import importlib
from unittest.mock import patch

import pytest


def test_module_imports():
    importlib.import_module("mcp_servers.trk_kubectl.server")


def test_read_verbs_whitelist():
    from mcp_servers.trk_kubectl.server import READ_VERBS, _run_kubectl
    assert "get" in READ_VERBS
    assert "logs" in READ_VERBS
    assert "describe" in READ_VERBS
    # Mutating verbs must not be in the whitelist
    for forbidden in ("apply", "delete", "create", "edit", "patch", "scale", "rollout"):
        assert forbidden not in READ_VERBS

    with pytest.raises(ValueError):
        _run_kubectl(["delete", "pod", "x"])


@pytest.mark.asyncio
async def test_tail_pod_logs_no_pods_match(monkeypatch):
    from mcp_servers.trk_kubectl import server as srv

    def fake_run(args, **_kwargs):
        # `kubectl get pods ... -o name` → no output → no pods
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr(srv.subprocess, "run", fake_run)
    out = await srv.call_tool(
        "tail_pod_logs",
        {"namespace": "trk", "label_selector": "app=missing"},
    )
    assert "no pods matching" in out[0].text


@pytest.mark.asyncio
async def test_tail_pod_logs_fans_out_across_pods(monkeypatch):
    from mcp_servers.trk_kubectl import server as srv

    calls = []

    def fake_run(args, **_kwargs):
        calls.append(args)
        class R:
            returncode = 0
            stderr = ""
        if args[1] == "get" and args[2] == "pods":
            R.stdout = "pod/ingress-1\npod/ingress-2\n"
        elif args[1] == "logs":
            R.stdout = f"log line for {args[3]}\n"
        else:
            R.stdout = ""
        return R()

    monkeypatch.setattr(srv.subprocess, "run", fake_run)
    out = await srv.call_tool(
        "tail_pod_logs",
        {"namespace": "trk", "label_selector": "app=ingress-service"},
    )
    text = out[0].text
    assert "ingress-1" in text
    assert "ingress-2" in text
    # At least 1 get-pods call + 1 per pod
    log_calls = [c for c in calls if c[1] == "logs"]
    assert len(log_calls) == 2


@pytest.mark.asyncio
async def test_unknown_tool_returns_error():
    from mcp_servers.trk_kubectl.server import call_tool
    out = await call_tool("not_a_real_tool", {})
    assert "unknown tool" in out[0].text
