"""RCA agent: filesystem-as-context investigation loop.

The agent uses the raw `anthropic.Anthropic` SDK rather than a higher-level
agent framework so we have explicit control over:
- max-tool-calls cap (12 per agent, 4 per sub-agent)
- writing every tool output to the per-investigation scratch dir
- forced synthesis with Opus 4.7 escalation if the loop hits the cap
- sub-agent dispatch via the `Task` tool

In-process MCP dispatch: each MCP server module exports a `call_tool(name,
arguments) -> list[TextContent]` async function. We import them once and
dispatch by name. This is faster than running each MCP server as a
subprocess and works the same way the MCP protocol does internally.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import AsyncIterator

from anthropic import Anthropic

from apps.api.agents.code_exec import run_code_exec_against_scratch
from apps.api.agents.scratch import Investigation
from apps.api.audit import TimedTool
from apps.api.settings import get_settings
from apps.api.sse import sse


_SYS_PATH = Path(__file__).parent.parent / "prompts" / "rca_system.md"
MAX_TOOL_CALLS = 18
SUBAGENT_MAX_CALLS = 4
MAX_SUBAGENTS = 3


def _new_rca_id() -> str:
    return f"rca_{uuid.uuid4().hex[:8]}"


# --- MCP tool dispatch table ---
# Each entry maps the agent's tool name to (module_path, op_name).
_MCP_DISPATCH: dict[str, tuple[str, str]] = {
    "sherlock_search":        ("mcp_servers.sherlock_rag.server",    "search"),
    "trk_postgres_query":     ("mcp_servers.trk_postgres.server",  "query_template"),
    "trk_postgres_list_types":("mcp_servers.trk_postgres.server",  "list_query_types"),
    "trk_cosmos_read":        ("mcp_servers.trk_cosmos.server",    "read_document"),
    "trk_cosmos_query":     ("mcp_servers.trk_cosmos.server",    "query_documents"),
    "trk_redis_get":        ("mcp_servers.trk_redis.server",     "redis_get"),
    "trk_kubectl_logs":     ("mcp_servers.trk_kubectl.server",   "tail_pod_logs"),
    "trk_kubectl_events":   ("mcp_servers.trk_kubectl.server",   "get_events"),
    "trk_kubectl_describe": ("mcp_servers.trk_kubectl.server",   "describe_pod"),
    "trk_kubectl_previous": ("mcp_servers.trk_kubectl.server",   "previous_logs"),
    "trk_datadog_search":   ("mcp_servers.trk_datadog.server",   "search_logs"),
    "trk_datadog_trace":    ("mcp_servers.trk_datadog.server",   "trace_correlation"),
}


def _datadog_available() -> bool:
    """Datadog tools are advertised to the agent only when both API + App key
    are populated. Without these, the trk-datadog MCP server can't authenticate,
    so we hide the tools entirely — saves tokens and prevents the agent from
    burning tool-call budget trying a path that would just fail."""
    s = get_settings()
    return bool(s.datadog_api_key and s.datadog_app_key)


def _tool_definitions() -> list[dict]:
    """Tool schemas published to Claude. Includes MCP tools + filesystem
    helpers + code_exec + Task + write_final_rca.

    Datadog tools are only included when DATADOG_API_KEY + DATADOG_APP_KEY
    are set — otherwise the agent uses kubectl as its only log source.
    """
    datadog_on = _datadog_available()
    all_tools = [
        {
            "name": "sherlock_search",
            "description": "Hybrid search over the indexed Trackonomy code+docs corpus. Returns chunks with file_path:line_range citations.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "service": {"type": "string"},
                    "category": {"type": "string"},
                    "top_k": {"type": "integer", "default": 8},
                },
                "required": ["query"],
            },
        },
        {
            "name": "trk_postgres_query",
            "description": (
                "Vetted parameterized SELECT over the trk PostgreSQL schema in the active env. "
                "Schema: trk (search_path=trk). Catalog (12 query_types): device_config, "
                "location_history, device_events_recent, raw_events_check, customer_config, "
                "feature_flags, facility_lookup, duplicate_check, device_health, "
                "event_delivery_check, account_lookup, application_lookup. "
                "Tenant filter: account_id (UUID) derived from customer_id + authorized_group. "
                "status/scan_type are ENUMs — do not compare as integers. "
                "Use trk_postgres_list_types to inspect param signatures."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query_type": {"type": "string"},
                    "params": {"type": "object"},
                },
                "required": ["query_type"],
            },
        },
        {
            "name": "trk_postgres_list_types",
            "description": "List all available PostgreSQL query_types and their required/optional parameter signatures.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "trk_cosmos_read",
            "description": "Read a Cosmos document by container + partition_key + id.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "container": {"type": "string"},
                    "partition_key": {"type": "array", "items": {"type": "string"}},
                    "id": {"type": "string"},
                },
                "required": ["container", "partition_key", "id"],
            },
        },
        {
            "name": "trk_cosmos_query",
            "description": "Run a SELECT-only Cosmos SQL-API query in a container.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "container": {"type": "string"},
                    "query": {"type": "string"},
                    "parameters": {"type": "array", "items": {"type": "object"}},
                    "max_items": {"type": "integer", "default": 10},
                },
                "required": ["container", "query"],
            },
        },
        {
            "name": "trk_redis_get",
            "description": "Read the active env's Redis by predefined key_type pattern: idict, pids_to_limes, ble_config, mesh_dedup, dwell_timer.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "key_type": {"type": "string"},
                    "params": {"type": "object"},
                    "member": {"type": "string"},
                },
                "required": ["key_type", "params"],
            },
        },
        {
            "name": "trk_kubectl_logs",
            "description": "Tail pod logs from the active env's AKS by label selector (fans out across replicas). PRIMARY log source — use this before Datadog.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string"},
                    "label_selector": {"type": "string"},
                    "since_seconds": {"type": "integer", "default": 600},
                    "max_lines_per_pod": {"type": "integer", "default": 200},
                },
                "required": ["namespace", "label_selector"],
            },
        },
        {
            "name": "trk_kubectl_events",
            "description": "Kubernetes events in a namespace (active env).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string"},
                    "since_seconds": {"type": "integer", "default": 600},
                },
                "required": ["namespace"],
            },
        },
        {
            "name": "trk_kubectl_describe",
            "description": "Describe a specific pod (status, conditions, container statuses).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string"},
                    "pod_name": {"type": "string"},
                },
                "required": ["namespace", "pod_name"],
            },
        },
        {
            "name": "trk_kubectl_previous",
            "description": "Fetch the previous (crashed) container's stdout for a pod that has restarted.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string"},
                    "pod_name": {"type": "string"},
                    "max_lines": {"type": "integer", "default": 200},
                },
                "required": ["namespace", "pod_name"],
            },
        },
        {
            "name": "trk_datadog_search",
            "description": "Search Datadog logs (FALLBACK for logs older than kubectl retention; Datadog is sunsetting).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "from_ts": {"type": "string"},
                    "to_ts": {"type": "string"},
                    "limit": {"type": "integer", "default": 50},
                },
                "required": ["query"],
            },
        },
        {
            "name": "trk_datadog_trace",
            "description": "Find all logs across services sharing a correlation_id.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "correlation_id": {"type": "string"},
                    "env": {"type": "string", "default": "ppe"},
                },
                "required": ["correlation_id"],
            },
        },
        {
            "name": "code_exec",
            "description": "Run Python in a sandbox over scratch-dir files. Pre-installed: pandas, matplotlib. Save outputs to /tmp/<name>.png or /tmp/<name>.mmd. NO database credentials inside.",
            "input_schema": {
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
            },
        },
        {
            "name": "Task",
            "description": (
                "Dispatch a sub-agent to investigate an independent branch. The sub-agent has the "
                "same MCP tools and shares this scratch dir. Use ONLY when threads are truly "
                "independent (e.g. 'check ingress logs' AND 'check rule-engine state' AND 'check "
                "Cosmos document'). Don't fan out for fan-out's sake."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "branch_name": {"type": "string"},
                    "instructions": {"type": "string"},
                },
                "required": ["branch_name", "instructions"],
            },
        },
        {
            "name": "write_final_rca",
            "description": "Write the synthesized RCA markdown to final-rca.md. Call EXACTLY ONCE when you're done. Stop investigating after.",
            "input_schema": {
                "type": "object",
                "properties": {"markdown": {"type": "string"}},
                "required": ["markdown"],
            },
        },
    ]

    if not datadog_on:
        all_tools = [t for t in all_tools if not t["name"].startswith("trk_datadog_")]
    # code_exec temporarily disabled: the Anthropic Code Execution + Files API
    # call passes `container` as a dict and now 400s ("container: Input should
    # be a valid string"). Removing it from the offered tools means the model
    # never calls it (no error in the trace); the RCA agent reasons directly
    # from the DB/corpus evidence, which produces complete RCAs without it.
    # TODO(post-demo): fix the Files-API/container integration and re-enable.
    all_tools = [t for t in all_tools if t["name"] != "code_exec"]
    return all_tools


async def _call_mcp_tool(tool_name: str, args: dict) -> str:
    """Dispatch by name to an in-process MCP server's call_tool handler."""
    if tool_name not in _MCP_DISPATCH:
        return f"unknown tool: {tool_name}"
    module_path, op = _MCP_DISPATCH[tool_name]
    mod = __import__(module_path, fromlist=["call_tool"])
    out = await mod.call_tool(op, args)
    return "\n".join(getattr(b, "text", "") for b in out)


def _system_prompt() -> str:
    return _SYS_PATH.read_text(encoding="utf-8")


def _content_to_history_block(resp_content) -> list[dict]:
    """Convert Anthropic response content into a serializable history block."""
    out = []
    for b in resp_content:
        if b.type == "text":
            out.append({"type": "text", "text": b.text})
        elif b.type == "tool_use":
            out.append({
                "type": "tool_use",
                "id": b.id,
                "name": b.name,
                "input": b.input,
            })
    return out


def _final_rca_only_tools() -> list[dict]:
    """Just the write_final_rca tool — used for the synthesis/escalation calls
    so Opus is forced to produce the report instead of firing another
    investigative tool when the tool-call budget is already spent."""
    return [t for t in _tool_definitions() if t["name"] == "write_final_rca"]


def _retry_user_message(resp_content, directive: str) -> dict:
    """Build the user message that follows a synthesis assistant turn on retry.

    The Anthropic API requires every tool_use in an assistant message to be
    answered by a tool_result in the IMMEDIATELY following message. If the
    synthesis response contained tool_use block(s) — e.g. an empty
    write_final_rca, or a stray investigative call — we must return a
    tool_result for each. A plain-text user message orphans the tool_use and
    400s the retry call ("tool_use ids were found without tool_result blocks").
    When the response had no tool_use, a plain-text directive is valid.
    """
    tool_uses = [b for b in resp_content if getattr(b, "type", None) == "tool_use"]
    if not tool_uses:
        return {"role": "user", "content": directive}
    return {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": b.id,
                "content": directive if i == 0
                else "Disregarded — call write_final_rca with the full markdown.",
            }
            for i, b in enumerate(tool_uses)
        ],
    }


async def _run_subagent(
    client: Anthropic,
    sys_prompt: str,
    inv: Investigation,
    branch_name: str,
    instructions: str,
) -> str:
    """Run a bounded sub-agent investigation; return its final text summary."""
    sub_history = [
        {
            "role": "user",
            "content": (
                f"You are a sub-agent investigating branch '{branch_name}' inside RCA "
                f"{inv.rca_id}. Scratch dir: {inv.dir}.\n\n"
                f"{instructions}\n\n"
                f"You have at most {SUBAGENT_MAX_CALLS} tool calls. End with a 1-2 sentence summary. "
                f"Do NOT call write_final_rca or Task; the parent agent will synthesize."
            ),
        }
    ]
    # Sub-agent's tools: everything except Task and write_final_rca
    sub_tools = [t for t in _tool_definitions() if t["name"] not in {"Task", "write_final_rca"}]

    calls = 0
    summary_parts: list[str] = []

    while calls < SUBAGENT_MAX_CALLS:
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            system=[{"type": "text", "text": sys_prompt, "cache_control": {"type": "ephemeral"}}],
            tools=sub_tools,
            messages=sub_history,
        )
        sub_history.append({"role": "assistant", "content": _content_to_history_block(resp.content)})

        for b in resp.content:
            if b.type == "text":
                summary_parts.append(b.text)

        any_tool = False
        sub_results: list[dict] = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            any_tool = True
            calls += 1
            try:
                if block.name == "code_exec":
                    code_result = run_code_exec_against_scratch(inv, block.input["code"])
                    out = (
                        f"stdout:\n{code_result['stdout']}\n\nstderr:\n{code_result['stderr']}\n\n"
                        f"produced files:\n" + "\n".join(str(p) for p in code_result["produced_files"])
                    )
                else:
                    out = await _call_mcp_tool(block.name, block.input)
                    ext = "json" if out.lstrip().startswith(("[", "{")) else "txt"
                    inv.write_evidence(name=f"{branch_name}-{block.name}", ext=ext, content=out)
            except Exception as e:
                out = f"tool error: {type(e).__name__}: {e}"
            sub_results.append(
                {"type": "tool_result", "tool_use_id": block.id, "content": out[:6000]}
            )

        if not any_tool:
            break
        sub_history.append({"role": "user", "content": sub_results})

    summary = "\n".join(summary_parts).strip() or "(no summary produced)"
    return f"[branch={branch_name}] {summary}"


async def run_rca(message: str, *, entities: dict | None = None) -> AsyncIterator[str]:
    """Stream SSE events for an RCA investigation."""
    s = get_settings()

    if not s.anthropic_api_key:
        yield sse(
            "status",
            {"phase": "blocked", "msg": "ANTHROPIC_API_KEY not set — RCA agent requires Claude."},
        )
        yield sse("done", {})
        return

    inv = Investigation.create(
        root=s.sherlock_investigations_dir,
        rca_id=_new_rca_id(),
        user_query=message,
        entities=entities or {},
    )

    yield sse("rca_started", {"rca_id": inv.rca_id, "scratch_dir": str(inv.dir)})

    client = Anthropic(api_key=s.anthropic_api_key)
    sys_prompt = _system_prompt()

    # Env preamble lives in the user message so the (cached) system prompt
    # stays identical across envs.
    from apps.api.env_context import active_env
    cfg = s.env_config(active_env.get() or s.sherlock_default_env)
    env_block = (
        "<env>\n"
        f"name: {cfg.env}\n"
        f"k8s_namespace: {cfg.k8s_namespace}\n"
        f"k8s_pod_suffix: {cfg.k8s_pod_suffix}\n"
        f"db: postgres  # schema=trk, host trk-mt-ppe-pgsql-eus2.postgres.database.azure.com; "
        f"tenant filter = account_id (UUID derived from customer_id+authorized_group); "
        f"use trk_postgres_query for all DB lookups.\n"
        "</env>\n\n"
    )

    # If the user's message EXPLICITLY mentions a different env than the UI
    # dropdown, flag it loudly. We trust the dropdown (active_env drives tools),
    # but the user might have meant the other env. Use a literal keyword check
    # rather than the router's `entities.env` (which silently defaults to "ppe"
    # when no signal exists, and would fire false-positive warnings).
    msg_lower = message.lower()
    mentioned_env: str | None = None
    for kw, name in [
        ("ppe", "ppe"), ("pre-prod", "ppe"),
        ("stage", "stage"), ("staging", "stage"),
        ("prod", "prod"), ("production", "prod"),
    ]:
        if re.search(rf"\b{re.escape(kw)}\b", msg_lower):
            mentioned_env = name
            break
    if mentioned_env and mentioned_env != cfg.env.lower():
        yield sse(
            "status",
            {
                "phase": "env_mismatch",
                "msg": (
                    f"Heads up — your message mentioned env={mentioned_env!r}, but the "
                    f"UI dropdown is set to env={cfg.env!r}. Tools will run against "
                    f"{cfg.env!r}. Switch the dropdown if you meant {mentioned_env!r}."
                ),
            },
        )

    history: list[dict] = [
        {
            "role": "user",
            "content": (
                env_block
                + f"Bug report: {message}\n\n"
                + f"Extracted entities: {json.dumps(entities or {})}\n\n"
                + f"Your scratch dir is `{inv.dir}`. Begin investigation."
            ),
        }
    ]

    tool_calls = 0
    subagents_dispatched = 0
    final_rca_written = False

    while tool_calls < MAX_TOOL_CALLS and not final_rca_written:
        try:
            resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4000,
                system=[{"type": "text", "text": sys_prompt, "cache_control": {"type": "ephemeral"}}],
                tools=_tool_definitions(),
                messages=history,
            )
        except Exception as e:
            yield sse("status", {"phase": "error", "msg": f"LLM error: {type(e).__name__}: {e}"})
            break

        for block in resp.content:
            if block.type == "text":
                yield sse("agent_text", {"text": block.text})

        history.append({"role": "assistant", "content": _content_to_history_block(resp.content)})

        tool_results: list[dict] = []
        any_tool = False

        for block in resp.content:
            if block.type != "tool_use":
                continue
            any_tool = True
            tool_calls += 1

            yield sse(
                "tool_call",
                {"id": block.id, "name": block.name, "args": block.input, "n": tool_calls},
            )

            t0 = time.monotonic()
            try:
                with TimedTool(session_id=None, rca_id=inv.rca_id, tool_name=block.name, args=block.input):
                    if block.name == "code_exec":
                        code_result = run_code_exec_against_scratch(inv, block.input["code"])
                        files_str = "\n".join(str(p) for p in code_result["produced_files"])
                        out_text = (
                            f"stdout:\n{code_result['stdout']}\n\n"
                            f"stderr:\n{code_result['stderr']}\n\n"
                            f"produced files:\n{files_str}"
                        )
                    elif block.name == "write_final_rca":
                        md = (block.input or {}).get("markdown") or ""
                        if md.strip():
                            inv.write_final_rca(md)
                            out_text = f"final-rca.md written ({len(md)} chars)"
                            final_rca_written = True
                        else:
                            # Model called the tool but didn't fill `markdown`.
                            # Don't crash — push it back to fix the call.
                            out_text = (
                                "ERROR: write_final_rca was called with an empty or missing "
                                "`markdown` field. Re-call write_final_rca with the FULL RCA "
                                "report as the `markdown` argument."
                            )
                    elif block.name == "Task":
                        if subagents_dispatched >= MAX_SUBAGENTS:
                            out_text = f"max sub-agents ({MAX_SUBAGENTS}) reached; do not call Task again"
                        else:
                            subagents_dispatched += 1
                            out_text = await _run_subagent(
                                client=client,
                                sys_prompt=sys_prompt,
                                inv=inv,
                                branch_name=block.input["branch_name"],
                                instructions=block.input["instructions"],
                            )
                    else:
                        out_text = await _call_mcp_tool(block.name, block.input)
                        ext = "json" if out_text.lstrip().startswith(("[", "{")) else "txt"
                        inv.write_evidence(name=block.name, ext=ext, content=out_text)
            except Exception as e:
                out_text = f"tool error: {type(e).__name__}: {e}"
            duration_ms = int((time.monotonic() - t0) * 1000)

            yield sse(
                "tool_result",
                {"id": block.id, "preview": out_text[:600], "duration_ms": duration_ms},
            )
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": out_text[:8000],
                }
            )

        if not any_tool:
            break

        # If write_final_rca was called this turn, exit before paying for
        # one more LLM round-trip just to get a "done" message. The while
        # predicate would catch this on the next iteration, but only after
        # client.messages.create() runs once more.
        if final_rca_written:
            break

        history.append({"role": "user", "content": tool_results})

    opus_text_chunks: list[str] = []
    opus_error: str | None = None
    if not final_rca_written:
        yield sse(
            "status",
            {
                "phase": "max_tool_calls",
                "msg": f"Reached {tool_calls}/{MAX_TOOL_CALLS} tool calls; forcing synthesis with Opus 4.7.",
            },
        )
        history.append(
            {
                "role": "user",
                "content": (
                    "You've reached the tool-call limit. Synthesize whatever evidence you have "
                    "via write_final_rca now. Even partial / inconclusive findings must be "
                    "delivered through write_final_rca — do not respond with text only."
                ),
            }
        )
        try:
            resp = client.messages.create(
                model="claude-opus-4-7",
                max_tokens=8000,
                system=[{"type": "text", "text": sys_prompt, "cache_control": {"type": "ephemeral"}}],
                tools=_final_rca_only_tools(),
                tool_choice={"type": "tool", "name": "write_final_rca"},
                messages=history,
            )
            for b in resp.content:
                if b.type == "tool_use" and b.name == "write_final_rca":
                    md = (b.input or {}).get("markdown") or ""
                    if md.strip():
                        inv.write_final_rca(md)
                        final_rca_written = True
                if b.type == "text":
                    opus_text_chunks.append(b.text)
                    yield sse("agent_text", {"text": b.text})

            # If Opus called the tool with an empty/missing markdown OR didn't
            # call it at all, give it ONE retry with a more directive prompt
            # that includes the evidence summary. This catches the common
            # failure where the model says "I have sufficient evidence" in
            # text and then fires an empty tool_use.
            if not final_rca_written:
                ev_summary = "\n".join(
                    f"  - {p.name}" for p in sorted(inv.list_evidence())[-12:]
                )
                history.append(
                    {"role": "assistant", "content": _content_to_history_block(resp.content)}
                )
                history.append(
                    _retry_user_message(
                        resp.content,
                        "You did NOT successfully call write_final_rca. The required "
                        "`markdown` argument was missing or empty. Call it ONCE more, "
                        "right now, with the complete RCA writeup as the `markdown` "
                        "string. "
                        f"Recent evidence files (in your scratch dir):\n{ev_summary}",
                    )
                )
                try:
                    retry = client.messages.create(
                        model="claude-opus-4-7",
                        max_tokens=8000,
                        system=[{"type": "text", "text": sys_prompt, "cache_control": {"type": "ephemeral"}}],
                        tools=_final_rca_only_tools(),
                        tool_choice={"type": "tool", "name": "write_final_rca"},
                        messages=history,
                    )
                    for b in retry.content:
                        if b.type == "tool_use" and b.name == "write_final_rca":
                            md = (b.input or {}).get("markdown") or ""
                            if md.strip():
                                inv.write_final_rca(md)
                                final_rca_written = True
                        if b.type == "text":
                            opus_text_chunks.append(b.text)
                except Exception as e2:
                    opus_error = f"retry {type(e2).__name__}: {e2}"
        except Exception as e:
            opus_error = f"{type(e).__name__}: {e}"
            yield sse(
                "status",
                {"phase": "synthesis_error", "msg": f"Opus synthesis failed: {opus_error}"},
            )

    # Last-resort fallback: if Opus didn't call write_final_rca (or errored),
    # still produce a final-rca.md so the user sees SOMETHING. Better a clear
    # "synthesis incomplete, here's what we found" report than an empty bubble.
    if not final_rca_written:
        stub = _build_synthesis_stub(
            inv=inv,
            user_query=message,
            entities=entities or {},
            cfg=cfg,
            tool_calls=tool_calls,
            subagents=subagents_dispatched,
            opus_text="\n\n".join(opus_text_chunks).strip(),
            opus_error=opus_error,
        )
        inv.write_final_rca(stub)
        final_rca_written = True
        yield sse(
            "status",
            {
                "phase": "synthesis_stub",
                "msg": "Wrote a synthesis stub — Opus didn't produce a complete RCA, so a partial summary was generated from the evidence collected.",
            },
        )

    yield sse(
        "rca_done",
        {
            "rca_id": inv.rca_id,
            "scratch_dir": str(inv.dir),
            "final_rca_path": str(inv.dir / "final-rca.md"),
            "evidence_count": len(inv.list_evidence()),
            "analysis_count": len(inv.list_analysis()),
            "tool_calls": tool_calls,
            "subagents": subagents_dispatched,
            "final_rca_written": final_rca_written,
        },
    )


def _build_synthesis_stub(
    *,
    inv: "Investigation",
    user_query: str,
    entities: dict,
    cfg,
    tool_calls: int,
    subagents: int,
    opus_text: str,
    opus_error: str | None,
) -> str:
    """Generate a fallback final-rca.md when Opus didn't synthesize one.

    Lists what the agent collected so the user can decide whether to retry or
    rephrase. Always shows the env mismatch (if any) — that's a common cause
    of empty results."""
    evidence = inv.list_evidence()
    msg_lower = user_query.lower()
    mentioned_env = None
    for kw, name in [
        ("ppe", "ppe"), ("pre-prod", "ppe"),
        ("stage", "stage"), ("staging", "stage"),
        ("prod", "prod"), ("production", "prod"),
    ]:
        if re.search(rf"\b{re.escape(kw)}\b", msg_lower):
            mentioned_env = name
            break
    env_warning = ""
    if mentioned_env and mentioned_env != cfg.env.lower():
        env_warning = (
            f"> ⚠ **Env mismatch:** Your message mentioned `env={mentioned_env}` but the "
            f"UI was set to `env={cfg.env}`. Tools ran against `{cfg.env}` — if the "
            f"bug actually happened in `{mentioned_env}`, switch the dropdown and re-ask.\n\n"
        )

    failure_reason = (
        f"Opus errored: `{opus_error}`" if opus_error
        else "Opus returned text but did not call `write_final_rca`."
    )

    body = [
        f"# Synthesis incomplete · {inv.rca_id}",
        "",
        env_warning + f"**Original question:**",
        "",
        f"> {user_query.strip()}",
        "",
        "## Why this is incomplete",
        "",
        f"After {tool_calls} tool calls and {subagents} sub-agent dispatches, "
        f"the synthesis step did not produce a complete RCA. {failure_reason}",
        "",
        "## What we collected",
        "",
        f"- {len(evidence)} evidence files in `{inv.dir.relative_to(inv.dir.parent)}/evidence/`",
        f"- Active env at runtime: `{cfg.env}` (k8s namespace `{cfg.k8s_namespace}`)",
    ]
    if entities:
        body.append(f"- Extracted entities: `{json.dumps(entities)}`")
    body.append("")

    if opus_text:
        body += [
            "## Opus's last text (no final write)",
            "",
            opus_text[:4000],
            "",
        ]

    body += [
        "## Suggested next steps",
        "",
        "- Verify the dropdown env matches the env where the bug actually occurred.",
        "- Re-ask with more specific details (tape_id, timestamps, exact error).",
        "- Check the evidence directory directly — many tool calls succeeded; "
        "the gap was in synthesis, not data collection.",
    ]
    return "\n".join(body)
