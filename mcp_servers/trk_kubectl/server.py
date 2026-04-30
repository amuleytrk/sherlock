"""trk-kubectl MCP server. Read-only kubectl wrapper, env-aware.

Tools:
- `tail_pod_logs(namespace, label_selector, since_seconds, max_lines_per_pod)`
- `get_events(namespace, since_seconds)`
- `list_deployments(namespace)`
- `describe_pod(namespace, pod_name)`
- `previous_logs(namespace, pod_name, max_lines)` — fetch the previous (crashed) container's stdout

The active env's KUBECONFIG is set per-subprocess via env injection — never
mutates the user's day-to-day kubectl context. Each env has its own
self-contained kubeconfig file (admin or SP-backed) so flipping envs in the UI
never depends on `az account set` or anything global.

Verbs are whitelisted; non-read verbs raise immediately.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from apps.api.env_context import EnvCreds, active_env
from apps.api.settings import get_settings


server = Server("trk-kubectl")


READ_VERBS = {"get", "describe", "logs", "top", "explain", "version"}


def _current_cfg() -> EnvCreds:
    s = get_settings()
    return s.env_config(active_env.get() or s.sherlock_default_env)


def _run_kubectl(args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    """Run `kubectl` with the given args. Verb (args[0]) must be in READ_VERBS.
    Injects the active env's KUBECONFIG into the subprocess, so concurrent
    requests on different envs don't collide."""
    if not args or args[0] not in READ_VERBS:
        raise ValueError(f"verb '{args[0] if args else ''}' is not in the read whitelist")
    cfg = _current_cfg()
    if not cfg.kubeconfig:
        return (
            2,
            "",
            f"kubectl not configured for env={cfg.env!r} — set "
            f"KUBECONFIG_{cfg.env.upper()} in .env to a self-contained kubeconfig path",
        )
    env = dict(os.environ)
    env["KUBECONFIG"] = cfg.kubeconfig
    try:
        res = subprocess.run(
            ["kubectl", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
        return res.returncode, res.stdout, res.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"kubectl timed out after {timeout}s"
    except FileNotFoundError:
        return 127, "", "kubectl not installed on PATH"


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="tail_pod_logs",
            description=(
                "Tail recent stdout from pods matching a label selector in a namespace. "
                "Fans out across all matching pods. Default: last 10 minutes, 200 lines per pod."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "namespace": {"type": "string"},
                    "label_selector": {"type": "string", "description": "e.g. app=ingress-service"},
                    "since_seconds": {"type": "integer", "default": 600},
                    "max_lines_per_pod": {"type": "integer", "default": 200},
                },
                "required": ["namespace", "label_selector"],
            },
        ),
        Tool(
            name="get_events",
            description="List Kubernetes events in a namespace, sorted by lastTimestamp.",
            inputSchema={
                "type": "object",
                "properties": {
                    "namespace": {"type": "string"},
                    "since_seconds": {"type": "integer", "default": 600},
                },
                "required": ["namespace"],
            },
        ),
        Tool(
            name="list_deployments",
            description="List Deployments in a namespace.",
            inputSchema={
                "type": "object",
                "properties": {"namespace": {"type": "string"}},
                "required": ["namespace"],
            },
        ),
        Tool(
            name="describe_pod",
            description="Describe a specific pod (status, conditions, events, container statuses).",
            inputSchema={
                "type": "object",
                "properties": {
                    "namespace": {"type": "string"},
                    "pod_name": {"type": "string"},
                },
                "required": ["namespace", "pod_name"],
            },
        ),
        Tool(
            name="previous_logs",
            description=(
                "Fetch the previous (crashed) container's stdout for a pod via `kubectl logs --previous`. "
                "Useful when a pod has restarted and the in-pod current logs don't show the failure."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "namespace": {"type": "string"},
                    "pod_name": {"type": "string"},
                    "max_lines": {"type": "integer", "default": 200},
                },
                "required": ["namespace", "pod_name"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name == "tail_pod_logs":
        return await _tail_pod_logs(arguments)
    if name == "get_events":
        return await _get_events(arguments)
    if name == "list_deployments":
        return await _list_deployments(arguments)
    if name == "describe_pod":
        return await _describe_pod(arguments)
    if name == "previous_logs":
        return await _previous_logs(arguments)
    return [TextContent(type="text", text=f"unknown tool: {name}")]


async def _tail_pod_logs(arguments: dict[str, Any]) -> list[TextContent]:
    ns = arguments["namespace"]
    sel = arguments["label_selector"]
    since = arguments.get("since_seconds", 600)
    max_lines = arguments.get("max_lines_per_pod", 200)

    rc, pods_out, pods_err = _run_kubectl(["get", "pods", "-n", ns, "-l", sel, "-o", "name"])
    if rc != 0:
        return [TextContent(type="text", text=f"kubectl get pods failed: {pods_err.strip()}")]
    pod_names = [p.split("/", 1)[-1] for p in pods_out.splitlines() if p.strip()]
    if not pod_names:
        return [TextContent(type="text", text=f"no pods matching {sel!r} in namespace {ns!r}")]

    out_chunks: list[str] = []
    for pod in pod_names:
        rc2, log_out, log_err = _run_kubectl(
            ["logs", "-n", ns, pod, f"--since={since}s", f"--tail={max_lines}"]
        )
        out_chunks.append(f"\n--- pod: {pod} ---")
        if rc2 != 0:
            out_chunks.append(f"(error: {log_err.strip()})")
        else:
            out_chunks.append(log_out)
    return [TextContent(type="text", text="\n".join(out_chunks))]


async def _get_events(arguments: dict[str, Any]) -> list[TextContent]:
    ns = arguments["namespace"]
    rc, out, err = _run_kubectl(
        ["get", "events", "-n", ns, "--sort-by=.lastTimestamp", "-o", "wide"]
    )
    return [TextContent(type="text", text=out if rc == 0 else f"error: {err.strip()}")]


async def _list_deployments(arguments: dict[str, Any]) -> list[TextContent]:
    ns = arguments["namespace"]
    rc, out, err = _run_kubectl(["get", "deployments", "-n", ns, "-o", "wide"])
    return [TextContent(type="text", text=out if rc == 0 else f"error: {err.strip()}")]


async def _describe_pod(arguments: dict[str, Any]) -> list[TextContent]:
    rc, out, err = _run_kubectl(
        ["describe", "pod", arguments["pod_name"], "-n", arguments["namespace"]]
    )
    return [TextContent(type="text", text=out if rc == 0 else f"error: {err.strip()}")]


async def _previous_logs(arguments: dict[str, Any]) -> list[TextContent]:
    rc, out, err = _run_kubectl(
        [
            "logs",
            "-n",
            arguments["namespace"],
            arguments["pod_name"],
            "--previous",
            f"--tail={arguments.get('max_lines', 200)}",
        ]
    )
    return [TextContent(type="text", text=out if rc == 0 else f"error: {err.strip()}")]


async def run():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())
