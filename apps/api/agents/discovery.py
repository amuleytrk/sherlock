"""Discovery agent: linear RAG → Sonnet 4.6 with grounded citations.

Flow:
1. Embed the user's query (OpenAI text-embedding-3-large).
2. Hybrid search (dense + tsvector) over pgvector with optional metadata filter.
3. Top-20 chunks become the `<knowledge_base>` context for Sonnet 4.6.
4. Stream the answer token-by-token.
"""
from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

from anthropic import Anthropic

from apps.api.settings import get_settings
from apps.api.sse import sse


_SYS_PATH = Path(__file__).parent.parent / "prompts" / "discovery_system.md"


def _format_kb(chunks: list[dict]) -> str:
    parts = []
    for c in chunks:
        heading = " > ".join(c.get("heading_hierarchy") or [])
        h = f' heading="{heading}"' if heading else ""
        parts.append(
            f'<chunk source="{c["file_path"]}:{c["line_start"]}-{c["line_end"]}" '
            f'service="{c["service"]}" category="{c["category"]}"{h}>\n'
            f'{c["content"]}\n'
            f"</chunk>"
        )
    return "\n\n".join(parts)


async def run_discovery(message: str, *, top_k: int = 20) -> AsyncIterator[str]:
    """Yield SSE events for a discovery query. The handler does retrieval first,
    streams retrieved-chunk citations, then streams the LLM answer tokens."""
    s = get_settings()

    # Lazy import: hybrid_search needs OpenAI to embed. If keys aren't set,
    # we surface a clear message rather than crashing.
    if not s.openai_api_key:
        yield sse(
            "status",
            {"phase": "blocked", "msg": "OPENAI_API_KEY not set — Discovery requires the embedding API."},
        )
        yield sse("done", {})
        return

    if not s.anthropic_api_key:
        yield sse(
            "status",
            {"phase": "blocked", "msg": "ANTHROPIC_API_KEY not set — Discovery requires Claude."},
        )
        yield sse("done", {})
        return

    yield sse("status", {"phase": "retrieving", "msg": "Searching corpus…"})

    try:
        from mcp_servers.sherlock_rag.server import hybrid_search
        chunks = hybrid_search(message, top_k=top_k, system=None)
    except Exception as e:
        yield sse("status", {"phase": "error", "msg": f"retrieval failed: {type(e).__name__}: {e}"})
        yield sse("done", {})
        return

    yield sse("status", {"phase": "retrieved", "msg": f"{len(chunks)} candidate chunks"})
    yield sse(
        "evidence",
        {
            "kind": "citation_list",
            "items": [
                {
                    "file_path": c["file_path"],
                    "line_start": c["line_start"],
                    "line_end": c["line_end"],
                    "service": c["service"],
                    "category": c["category"],
                }
                for c in chunks[:8]
            ],
        },
    )

    sys_prompt = _SYS_PATH.read_text(encoding="utf-8")
    kb = _format_kb(chunks)

    yield sse("status", {"phase": "generating", "msg": "Composing grounded answer…"})

    user_preamble = (
        "<context>\n"
        "db: postgres  # schema=trk (trk.device, trk.device_event, trk.account, etc.); "
        "discuss only PostgreSQL-relevant tables/queries.\n"
        "</context>\n\n"
    )

    client = Anthropic(api_key=s.anthropic_api_key)
    answer_buf: list[str] = []
    with client.messages.stream(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        system=[
            {"type": "text", "text": sys_prompt, "cache_control": {"type": "ephemeral"}},
        ],
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"<knowledge_base>\n{kb}\n</knowledge_base>"},
                    {"type": "text", "text": user_preamble + message},
                ],
            }
        ],
    ) as stream:
        for text in stream.text_stream:
            answer_buf.append(text)
            yield sse("answer_delta", {"text": text})

    # Trust layer: verify the assembled answer against the KB chunks. Wrapped
    # in try/except so a verifier failure never blocks the answer.
    answer_md = "".join(answer_buf)
    try:
        from apps.api.verify import verify_answer
        yield sse("status", {"phase": "verifying", "msg": "Self-checking claims against citations…"})
        verification = verify_answer(answer_md, kb_text=kb)
        yield sse("verification", verification)
    except Exception as e:
        # Soft failure — emit a neutral verification so the UI can still show
        # something rather than nothing.
        yield sse("verification", {
            "score": 60, "band": "yellow", "claims": [],
            "extracted_count": 0, "note": f"verifier error: {type(e).__name__}",
        })

    yield sse("done", {})
