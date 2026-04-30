"""Parallel log fetcher for cross-service trace.

Critical perf optimization: trk_kubectl's existing `tail_pod_logs` runs
sequentially across pods. For a trace across 5 services with 1-2 pods each,
that's ~25s of wallclock latency. By fanning out via `asyncio.to_thread` +
`asyncio.gather`, we collapse that to ~6s (limited by the slowest single pod).
"""
from __future__ import annotations

import asyncio
import os
import subprocess
from dataclasses import dataclass

from apps.api.env_context import EnvCreds


@dataclass
class ServiceLogs:
    service: str
    label_selector: str
    pod_count: int
    raw_log: str        # concatenated stdout from all pod logs in the service
    error: str | None = None


def _run_kubectl_blocking(cfg: EnvCreds, args: list[str], timeout: int = 25) -> tuple[int, str, str]:
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


def _fetch_one_service_blocking(
    cfg: EnvCreds, service: str, since_seconds: int, max_lines_per_pod: int,
) -> ServiceLogs:
    label = f"app={service}{cfg.k8s_pod_suffix}"
    rc, pods_out, pods_err = _run_kubectl_blocking(
        cfg, ["get", "pods", "-n", cfg.k8s_namespace, "-l", label, "-o", "name"],
        timeout=15,
    )
    if rc != 0:
        return ServiceLogs(service=service, label_selector=label, pod_count=0,
                           raw_log="", error=pods_err.strip()[:200])
    pod_names = [p.split("/", 1)[-1] for p in pods_out.splitlines() if p.strip()]
    if not pod_names:
        return ServiceLogs(service=service, label_selector=label, pod_count=0, raw_log="")

    chunks: list[str] = []
    for pod in pod_names[:3]:  # bound the fan-out per service
        rc2, log_out, _ = _run_kubectl_blocking(
            cfg,
            ["logs", "-n", cfg.k8s_namespace, pod,
             f"--since={since_seconds}s", f"--tail={max_lines_per_pod}",
             "--timestamps"],
            timeout=30,
        )
        if rc2 == 0:
            chunks.append(log_out)
    return ServiceLogs(
        service=service, label_selector=label,
        pod_count=len(pod_names), raw_log="\n".join(chunks),
    )


async def fetch_logs_parallel(
    cfg: EnvCreds, services: list[str], *,
    since_seconds: int = 3600,
    max_lines_per_pod: int = 2000,
) -> list[ServiceLogs]:
    """Fan out log retrieval across services. Each service's call still does
    a small serial loop over its 1-3 pods (cheap), but services run in parallel.
    """
    coros = [
        asyncio.to_thread(
            _fetch_one_service_blocking,
            cfg, svc, since_seconds, max_lines_per_pod,
        )
        for svc in services
    ]
    return await asyncio.gather(*coros)
