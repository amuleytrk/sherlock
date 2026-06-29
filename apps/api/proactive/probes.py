"""Proactive health probes — small, deterministic signals that something is
wrong. Each probe is a function that takes the active env context, runs ONE
read-only kubectl call (with a tight timeout), and returns a structured result.

Probes are intentionally SHALLOW. If a probe fires, the briefing orchestrator
launches a focused mini-RCA against that signal. The probes themselves don't
need to explain why — just notice.

Severity bands:
- green:  no anomaly
- yellow: noteworthy but non-blocking (e.g. <5 errors/h, <3 pod restarts)
- red:    likely live incident (CrashLoopBackOff, repeated insert failures)
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Callable

from apps.api.env_context import EnvCreds


@dataclass
class ProbeResult:
    name: str
    severity: str = "green"     # 'green' | 'yellow' | 'red'
    anomaly: bool = False
    summary: str = ""
    evidence: list[str] = field(default_factory=list)
    details: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "severity": self.severity,
            "anomaly": self.anomaly,
            "summary": self.summary,
            "evidence": self.evidence,
            "details": self.details,
        }


def _run_kubectl(cfg: EnvCreds, args: list[str], timeout: int = 20) -> tuple[int, str, str]:
    """Run kubectl with the env-specific KUBECONFIG. Mirrors the trk_kubectl
    server but is callable from the probe code without going through the MCP
    contextvar dance."""
    if not cfg.kubeconfig or not os.path.isfile(cfg.kubeconfig):
        return (2, "", f"KUBECONFIG_{cfg.env.upper()} not configured")
    env = dict(os.environ)
    env["KUBECONFIG"] = cfg.kubeconfig
    try:
        res = subprocess.run(
            ["kubectl", *args],
            capture_output=True, text=True, timeout=timeout, env=env, check=False,
        )
        return res.returncode, res.stdout, res.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"kubectl timed out after {timeout}s"
    except FileNotFoundError:
        return 127, "", "kubectl not installed on PATH"


# --- Individual probes ---


def probe_pod_restarts(cfg: EnvCreds) -> ProbeResult:
    """Pods with restartCount > 3 in the last 24h. Yellow if any; red if any
    are CrashLoopBackOff."""
    rc, out, err = _run_kubectl(cfg, ["get", "pods", "-n", cfg.k8s_namespace, "-o", "json"])
    if rc != 0:
        return ProbeResult(name="pod_restarts", summary=f"probe failed: {err.strip()[:120]}")
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return ProbeResult(name="pod_restarts", summary="kubectl returned non-JSON")

    bad: list[tuple[str, int, bool]] = []  # (pod_name, restarts, crashlooping)
    for item in data.get("items", []):
        name = item.get("metadata", {}).get("name", "?")
        statuses = item.get("status", {}).get("containerStatuses", []) or []
        for cs in statuses:
            restarts = cs.get("restartCount", 0)
            waiting = (cs.get("state") or {}).get("waiting") or {}
            crashlooping = waiting.get("reason") == "CrashLoopBackOff"
            if restarts > 3 or crashlooping:
                bad.append((name, restarts, crashlooping))
                break

    if not bad:
        return ProbeResult(name="pod_restarts", summary="0 pods with elevated restart count")

    severity = "red" if any(c for _, _, c in bad) else "yellow"
    bad.sort(key=lambda x: x[1], reverse=True)
    evidence = [
        f"{n} ({r} restarts{', CrashLoopBackOff' if c else ''})"
        for n, r, c in bad[:6]
    ]
    return ProbeResult(
        name="pod_restarts",
        severity=severity,
        anomaly=True,
        summary=f"{len(bad)} pod(s) with elevated restart count in {cfg.k8s_namespace}",
        evidence=evidence,
    )


def _grep_logs_for(
    cfg: EnvCreds,
    label_selector: str,
    pattern: re.Pattern,
    since_seconds: int = 3600,
    max_lines_per_pod: int = 1000,
) -> tuple[int, list[str]]:
    """Pull recent logs for a service and grep for a pattern. Returns
    (match_count, sampled_lines). Bounded so a noisy service doesn't blow up
    the briefing run."""
    rc, pods_out, _ = _run_kubectl(
        cfg, ["get", "pods", "-n", cfg.k8s_namespace, "-l", label_selector, "-o", "name"]
    )
    if rc != 0 or not pods_out.strip():
        return 0, []
    pod_names = [p.split("/", 1)[-1] for p in pods_out.splitlines() if p.strip()]

    matches = 0
    samples: list[str] = []
    for pod in pod_names[:4]:  # bound the fan-out
        rc2, log_out, _ = _run_kubectl(
            cfg,
            ["logs", "-n", cfg.k8s_namespace, pod,
             f"--since={since_seconds}s", f"--tail={max_lines_per_pod}"],
            timeout=25,
        )
        if rc2 != 0:
            continue
        for line in log_out.splitlines():
            if pattern.search(line):
                matches += 1
                if len(samples) < 3:
                    samples.append(line[:280])
    return matches, samples


# PG-era: device_event INSERTs are performed by location-preprocessor.
# The old MSSQL string "insertMilestoneLookup :: Error" does not exist in PG code.
# Primary target: location-preprocessor (normal 5264/5258 device_event failures).
# Secondary target: ingress-service (Brinks milestone failures via insertMilestoneEvent).
_DEVICE_EVENT_INSERT_FAIL = re.compile(
    r"Error inserting lookup parcel entry for tape:", re.IGNORECASE
)
_MILESTONE_BRINKS_FAIL = re.compile(
    r"insertMilestoneEvent", re.IGNORECASE
)


def probe_milestone_insert_failures(cfg: EnvCreds) -> ProbeResult:
    """device_event INSERT failures in the past hour.

    Primary check: location-preprocessor — this service owns the device_event
    (lookup_parcels) write in PG for normal 5264/5258 events. Looks for
    "Error inserting lookup parcel entry for tape:" log lines.

    Secondary check: ingress-service (Brinks milestone path via insertMilestoneEvent).

    Yellow at 1+ total matches, red at 5+.
    """
    # Primary: location-preprocessor (normal device_event INSERT failures)
    loc_label = f"app=location-preprocessor-service{cfg.k8s_pod_suffix}"
    loc_count, loc_samples = _grep_logs_for(cfg, loc_label, _DEVICE_EVENT_INSERT_FAIL, since_seconds=3600)

    # Secondary: ingress-service (Brinks milestone INSERT failures)
    ing_label = f"app=ingress-service{cfg.k8s_pod_suffix}"
    ing_count, ing_samples = _grep_logs_for(cfg, ing_label, _MILESTONE_BRINKS_FAIL, since_seconds=3600)

    count = loc_count + ing_count
    samples = [f"[location-preprocessor] {l}" for l in loc_samples] + \
              [f"[ingress-service] {l}" for l in ing_samples]

    if count == 0:
        return ProbeResult(
            name="milestone_insert_failures",
            summary="0 device_event insert failures in the past hour",
        )
    severity = "red" if count >= 5 else "yellow"
    return ProbeResult(
        name="milestone_insert_failures",
        severity=severity,
        anomaly=True,
        summary=f"{count} device_event insert failure(s) in past 1h (location-preprocessor: {loc_count}, ingress-service: {ing_count})",
        evidence=samples[:5],
    )


_REDIS_SOCKET = re.compile(r"Redis (client: Error|connection.*closed)|Socket closed unexpectedly", re.IGNORECASE)


def probe_redis_socket_errors(cfg: EnvCreds) -> ProbeResult:
    """Redis socket-closed events across pipeline services in the past hour.

    Covers the full device_event pipeline: location-preprocessor and
    event-preprocessor-service (PG-era writers) plus ingress-service,
    external-service, and device-management-service (milestone path).
    Some churn is normal; threshold at 5+ for yellow, 20+ for red.
    """
    services = [
        "location-preprocessor-service",   # device_event writer (PG-era primary)
        "event-preprocessor-service",       # raw_device_event writer
        "ingress-service",
        "external-service",
        "device-management-service",
    ]
    total = 0
    samples: list[str] = []
    for svc in services:
        label = f"app={svc}{cfg.k8s_pod_suffix}"
        c, s = _grep_logs_for(cfg, label, _REDIS_SOCKET, since_seconds=3600, max_lines_per_pod=500)
        total += c
        for line in s:
            samples.append(f"[{svc}] {line[:240]}")
        if len(samples) > 5:
            break
    if total == 0:
        return ProbeResult(
            name="redis_socket_errors",
            summary="0 Redis socket errors across pipeline services",
        )
    severity = "red" if total >= 20 else ("yellow" if total >= 5 else "green")
    return ProbeResult(
        name="redis_socket_errors",
        severity=severity,
        anomaly=severity != "green",
        summary=f"{total} Redis socket-closed event(s) in past 1h across pipeline services",
        evidence=samples[:5],
    )


_5XX_PATTERN = re.compile(r"\b(50[0-9])\b.*(?:error|failed|exception)", re.IGNORECASE)


def probe_ingress_5xx(cfg: EnvCreds) -> ProbeResult:
    """5xx-shaped errors in ingress-service in the past hour. Common during
    rollouts; threshold higher than the others."""
    label = f"app=ingress-service{cfg.k8s_pod_suffix}"
    count, samples = _grep_logs_for(cfg, label, _5XX_PATTERN, since_seconds=3600)
    if count == 0:
        return ProbeResult(name="ingress_5xx", summary="0 5xx-shaped errors in ingress-service in past 1h")
    severity = "red" if count >= 25 else ("yellow" if count >= 5 else "green")
    return ProbeResult(
        name="ingress_5xx",
        severity=severity,
        anomaly=severity != "green",
        summary=f"{count} 5xx-shaped log line(s) in ingress-service in past 1h",
        evidence=samples,
    )


# Exported in the order they should appear in the briefing.
PROBES: list[tuple[str, Callable[[EnvCreds], ProbeResult]]] = [
    ("pod_restarts", probe_pod_restarts),
    ("milestone_insert_failures", probe_milestone_insert_failures),
    ("redis_socket_errors", probe_redis_socket_errors),
    ("ingress_5xx", probe_ingress_5xx),
]


async def run_all_probes(cfg: EnvCreds) -> list[ProbeResult]:
    """Run every probe in parallel. Each probe is itself a blocking subprocess
    call, so we offload via asyncio.to_thread."""
    coros = [asyncio.to_thread(fn, cfg) for _, fn in PROBES]
    return await asyncio.gather(*coros)
