"""Tests for the proactive briefing pipeline. Probes are mocked at the
kubectl-subprocess boundary so the suite stays network-free."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from apps.api.env_context import EnvCreds
from apps.api.proactive import probes


def _cfg() -> EnvCreds:
    return EnvCreds(
        env="ppe", kubeconfig="/tmp/sherlock-fake-kube",
        k8s_namespace="ppe", k8s_pod_suffix="-ppe",
    )


def _patch_run_kubectl(monkeypatch, fake):
    monkeypatch.setattr(probes, "_run_kubectl", fake)
    # Make the kubeconfig file existence check pass.
    Path("/tmp/sherlock-fake-kube").touch()


def test_probe_pod_restarts_clean(monkeypatch):
    """No pods with elevated restart counts → green."""
    def fake(cfg, args, timeout=20):
        return 0, json.dumps({"items": [
            {"metadata": {"name": "ingress-1"},
             "status": {"containerStatuses": [{"restartCount": 0,
                                               "state": {"running": {}}}]}}
        ]}), ""
    _patch_run_kubectl(monkeypatch, fake)
    r = probes.probe_pod_restarts(_cfg())
    assert r.severity == "green"
    assert not r.anomaly


def test_probe_pod_restarts_yellow(monkeypatch):
    """4 restarts → yellow."""
    def fake(cfg, args, timeout=20):
        return 0, json.dumps({"items": [
            {"metadata": {"name": "ingress-1"},
             "status": {"containerStatuses": [{"restartCount": 5,
                                               "state": {"running": {}}}]}}
        ]}), ""
    _patch_run_kubectl(monkeypatch, fake)
    r = probes.probe_pod_restarts(_cfg())
    assert r.severity == "yellow"
    assert r.anomaly
    assert "ingress-1" in " ".join(r.evidence)


def test_probe_pod_restarts_red_on_crashloop(monkeypatch):
    """CrashLoopBackOff → red regardless of restart count."""
    def fake(cfg, args, timeout=20):
        return 0, json.dumps({"items": [
            {"metadata": {"name": "auth-x"},
             "status": {"containerStatuses": [{"restartCount": 2,
                                               "state": {"waiting": {"reason": "CrashLoopBackOff"}}}]}}
        ]}), ""
    _patch_run_kubectl(monkeypatch, fake)
    r = probes.probe_pod_restarts(_cfg())
    assert r.severity == "red"
    assert "CrashLoopBackOff" in r.evidence[0]


def test_probe_milestone_failures_zero(monkeypatch):
    """No matching log lines → green."""
    def fake(cfg, args, timeout=20):
        if "get" in args and "pods" in args:
            return 0, "pod/ingress-1\n", ""
        return 0, "no errors here\nplain log line\n", ""
    _patch_run_kubectl(monkeypatch, fake)
    r = probes.probe_milestone_insert_failures(_cfg())
    assert r.severity == "green"


def test_probe_milestone_failures_red(monkeypatch):
    def fake(cfg, args, timeout=20):
        if "get" in args and "pods" in args:
            return 0, "pod/ingress-1\n", ""
        log = "\n".join([
            'insertMilestoneLookup :: Error inserting milestone lookup :: oops',
        ] * 6)
        return 0, log, ""
    _patch_run_kubectl(monkeypatch, fake)
    r = probes.probe_milestone_insert_failures(_cfg())
    assert r.severity == "red"
    assert r.anomaly


def test_probe_milestone_failures_kubeconfig_missing(monkeypatch):
    """No kubeconfig → fail-soft summary, not an exception."""
    def fake(cfg, args, timeout=20):
        return 2, "", "KUBECONFIG_PPE not configured"
    _patch_run_kubectl(monkeypatch, fake)
    r = probes.probe_milestone_insert_failures(_cfg())
    assert not r.anomaly  # pod listing failed silently


@pytest.mark.asyncio
async def test_run_all_probes_executes_each(monkeypatch):
    """Smoke: run_all_probes invokes every probe in the registry."""
    seen: list[str] = []
    def fake(cfg, args, timeout=20):
        seen.append(args[0] if args else "")
        if args[:2] == ["get", "pods"] and "-o" in args and "json" in args:
            return 0, '{"items": []}', ""
        if "get" in args and "pods" in args:
            return 0, "", ""
        return 0, "", ""
    _patch_run_kubectl(monkeypatch, fake)
    results = await probes.run_all_probes(_cfg())
    names = sorted(r.name for r in results)
    assert names == sorted(n for n, _ in probes.PROBES)
