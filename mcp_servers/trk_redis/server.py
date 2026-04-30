"""trk-redis MCP server.

Read-only key lookups against the active env's Azure Redis Cache. Only the
operations GET / HGETALL / EXISTS / ZSCORE are exposed via predefined key
patterns — no arbitrary commands accepted.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import redis

from apps.api.env_context import EnvCreds, active_env
from apps.api.settings import get_settings
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool


server = Server("trk-redis")


KEY_PATTERNS: dict[str, dict] = {
    "idict": {
        "pattern": "iDict:{tape_id}",
        "op": "HGETALL",
        "params": ["tape_id"],
        "doc": "Per-device cached config: customer, authorized_groups, facility, displayname, personality.",
    },
    "pids_to_limes": {
        "pattern": "pidsToLimeIds:{facility_id}",
        "op": "HGETALL",
        "params": ["facility_id"],
        "doc": "PID-to-lime-id map for a facility — used by lime selection algorithm.",
    },
    "ble_config": {
        "pattern": "bleConfig:{gateway_mac_id}",
        "op": "GET",
        "params": ["gateway_mac_id"],
        "doc": "BLE config JSON for a gateway.",
    },
    "mesh_dedup": {
        "pattern": "meshDeduping:{device_id}:{g1}",
        "op": "EXISTS",
        "params": ["device_id", "g1"],
        "doc": "Mesh dedup marker: true if this G1 packet was already processed in the last 5 min.",
    },
    "dwell_timer": {
        "pattern": "ZONETIMER_{customer_id}:{authorized_group}:{tape_id}:{zone_id}",
        "op": "ZSCORE",
        "params": ["customer_id", "authorized_group", "tape_id", "zone_id"],
        "doc": "Dwell timer entry's score. Requires `member` arg too (the score sub-key).",
    },
}


def _current_cfg() -> EnvCreds:
    s = get_settings()
    return s.env_config(active_env.get() or s.sherlock_default_env)


def _client(cfg: EnvCreds):
    """Build a read-side Redis client for the active env.

    Two configuration shapes are accepted:
    1. `REDIS_<ENV>_HOST` + `_PORT` + `_KEY` (preferred — matches how Trackonomy
       backend services configure Redis; avoids URL-encoding headaches with
       passwords that contain `+`, `/`, `=`).
    2. `REDIS_<ENV>_URL` (single connection string, e.g. `rediss://:KEY@host:port`).

    If both are set, the host/port/key triplet wins.

    SSL: we explicitly point at `certifi`'s CA bundle. Python on macOS often
    can't find the system trust store, so `redis.Redis(ssl=True)` alone fails
    with "unable to get local issuer certificate" against Azure Redis.
    """
    import certifi
    if cfg.redis_host and cfg.redis_key:
        kwargs = {
            "host": cfg.redis_host,
            "port": cfg.redis_port,
            "password": cfg.redis_key,
            "decode_responses": True,
            "socket_timeout": 5,
            "socket_connect_timeout": 5,
        }
        if cfg.redis_tls:
            kwargs["ssl"] = True
            kwargs["ssl_ca_certs"] = certifi.where()
        return redis.Redis(**kwargs)
    if cfg.redis_url:
        if cfg.redis_url.startswith("rediss://"):
            return redis.from_url(
                cfg.redis_url,
                decode_responses=True,
                ssl_ca_certs=certifi.where(),
            )
        return redis.from_url(cfg.redis_url, decode_responses=True)
    raise RuntimeError(
        f"Redis not configured for env={cfg.env!r}: set "
        f"REDIS_{cfg.env.upper()}_HOST + _PORT + _KEY (preferred), or REDIS_{cfg.env.upper()}_URL"
    )


@server.list_tools()
async def list_tools() -> list[Tool]:
    catalog = "\n".join(
        f"  - {kt}({', '.join(spec['params'])}) → {spec['op']}: {spec['doc']}"
        for kt, spec in sorted(KEY_PATTERNS.items())
    )
    return [
        Tool(
            name="redis_get",
            description=(
                "Look up a Redis key via predefined pattern. Read-only — only GET/HGETALL/EXISTS/"
                "ZSCORE supported.\n"
                f"Patterns:\n{catalog}"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "key_type": {"type": "string", "enum": list(KEY_PATTERNS.keys())},
                    "params": {
                        "type": "object",
                        "description": "Values to fill into the key pattern",
                    },
                    "member": {
                        "type": "string",
                        "description": "ZSCORE member arg (required for dwell_timer)",
                    },
                },
                "required": ["key_type", "params"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name != "redis_get":
        return [TextContent(type="text", text=f"unknown tool: {name}")]

    cfg = _current_cfg()
    if not (cfg.redis_url or (cfg.redis_host and cfg.redis_key)):
        return [TextContent(
            type="text",
            text=(
                f"redis not configured for env={cfg.env!r} — set "
                f"REDIS_{cfg.env.upper()}_HOST + _PORT + _KEY, or REDIS_{cfg.env.upper()}_URL"
            ),
        )]

    kt = arguments.get("key_type")
    if kt not in KEY_PATTERNS:
        return [TextContent(type="text", text=f"unknown key_type: {kt}")]
    spec = KEY_PATTERNS[kt]

    try:
        key = spec["pattern"].format(**(arguments.get("params") or {}))
    except KeyError as e:
        return [TextContent(type="text", text=f"missing param for key pattern: {e}")]

    op = spec["op"]
    try:
        client = _client(cfg)
        if op == "GET":
            val = client.get(key)
        elif op == "HGETALL":
            val = client.hgetall(key)
        elif op == "EXISTS":
            val = bool(client.exists(key))
        elif op == "ZSCORE":
            member = arguments.get("member")
            if not member:
                return [TextContent(type="text", text="ZSCORE requires `member`")]
            val = client.zscore(key, member)
        else:
            return [TextContent(type="text", text=f"unsupported op: {op}")]
        return [TextContent(
            type="text",
            text=json.dumps({"key": key, "op": op, "value": val}, default=str, indent=2),
        )]
    except Exception as e:
        return [TextContent(type="text", text=f"redis error: {type(e).__name__}: {e}")]


async def run():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())
