"""Convert MarkdownBlocks (and CodeBlocks) into Chunk records.

A Chunk is the unit of indexing: stable ID, deterministic content hash, and
all metadata needed to filter retrieval. Oversized blocks are split on
paragraph boundaries with a tiktoken budget.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

import tiktoken

from indexer.parse import MarkdownBlock


_enc = tiktoken.get_encoding("cl100k_base")


@dataclass
class Chunk:
    chunk_id: str
    release: str
    service: str
    category: str
    file_path: str
    line_start: int
    line_end: int
    heading_hierarchy: list[str]
    content: str
    token_count: int
    parent_id: str | None = None
    # 'mssql' | 'postgres' | 'both'. Drives the UI's MSSQL/Postgres dropdown
    # so retrieval can scope to one DB era.
    system: str = "both"


def chunk_id_for(file_path: str, line_start: int, line_end: int, release: str) -> str:
    h = sha256(f"{file_path}:{line_start}:{line_end}:{release}".encode()).hexdigest()
    return h[:32]


def chunk_markdown(
    blocks: list[MarkdownBlock],
    release: str,
    service: str = "platform",
    category: str = "architecture",
    max_tokens: int = 1000,
    system: str = "both",
) -> list[Chunk]:
    by_heading_path: dict[tuple[str, ...], str] = {}
    out: list[Chunk] = []

    for b in blocks:
        token_ids = _enc.encode(b.content)
        parent_id = None
        if b.parent_heading and len(b.heading_hierarchy) > 1:
            parent_path = tuple(b.heading_hierarchy[:-1])
            parent_id = by_heading_path.get(parent_path)

        if len(token_ids) <= max_tokens:
            cid = chunk_id_for(b.file_path, b.line_start, b.line_end, release)
            chunks_for_block = [
                Chunk(
                    chunk_id=cid,
                    release=release,
                    service=service,
                    category=category,
                    file_path=b.file_path,
                    line_start=b.line_start,
                    line_end=b.line_end,
                    heading_hierarchy=b.heading_hierarchy,
                    content=b.content,
                    token_count=len(token_ids),
                    parent_id=parent_id,
                    system=system,
                )
            ]
        else:
            chunks_for_block = _split_oversized(b, release, service, category, max_tokens, parent_id, system)

        out.extend(chunks_for_block)
        # Record the first chunk's id under this heading path for child lookups
        by_heading_path[tuple(b.heading_hierarchy)] = chunks_for_block[0].chunk_id

    return out


def _split_oversized(
    b: MarkdownBlock,
    release: str,
    service: str,
    category: str,
    max_tokens: int,
    parent_id: str | None,
    system: str = "both",
) -> list[Chunk]:
    parts = b.content.split("\n\n")
    out: list[Chunk] = []
    buf: list[str] = []
    buf_tokens = 0
    line_cursor = b.line_start

    def flush(piece_index: int) -> None:
        nonlocal buf, buf_tokens, line_cursor
        if not buf:
            return
        text = "\n\n".join(buf)
        line_end = min(line_cursor + text.count("\n"), b.line_end)
        cid = chunk_id_for(b.file_path, line_cursor, line_end, f"{release}:p{piece_index}")
        out.append(
            Chunk(
                chunk_id=cid,
                release=release,
                service=service,
                category=category,
                file_path=b.file_path,
                line_start=line_cursor,
                line_end=line_end,
                heading_hierarchy=b.heading_hierarchy,
                content=text,
                token_count=buf_tokens,
                parent_id=parent_id,
                system=system,
            )
        )
        line_cursor = line_end + 1
        buf = []
        buf_tokens = 0

    for para in parts:
        ptok_ids = _enc.encode(para)
        ptok = len(ptok_ids)

        # If a single paragraph alone exceeds the budget, slice its tokens.
        if ptok > max_tokens:
            if buf:
                flush(piece_index=len(out))
            for start in range(0, ptok, max_tokens):
                slice_ids = ptok_ids[start:start + max_tokens]
                buf = [_enc.decode(slice_ids)]
                buf_tokens = len(slice_ids)
                flush(piece_index=len(out))
            continue

        if buf and buf_tokens + ptok > max_tokens:
            flush(piece_index=len(out))
        buf.append(para)
        buf_tokens += ptok
    flush(piece_index=len(out))

    return out


@dataclass
class _MinimalCodeBlock:
    """Duck-type for chunk_code consumers — actual CodeBlock lives in parse_code.py."""
    file_path: str
    name: str
    line_start: int
    line_end: int
    content: str


def chunk_code(
    blocks: list,
    release: str,
    service: str,
    category: str,
    max_tokens: int = 1200,
    system: str = "both",
) -> list[Chunk]:
    """Convert parse_code.CodeBlock instances into Chunks (one per method/function)."""
    out: list[Chunk] = []
    for b in blocks:
        token_ids = _enc.encode(b.content)
        truncated = len(token_ids) > max_tokens
        if truncated:
            content = _enc.decode(token_ids[:max_tokens])
            tc = max_tokens
            heading = [b.name + " (truncated)"]
        else:
            content = b.content
            tc = len(token_ids)
            heading = [b.name]

        out.append(
            Chunk(
                chunk_id=chunk_id_for(b.file_path, b.line_start, b.line_end, release),
                release=release,
                service=service,
                category=category,
                file_path=b.file_path,
                line_start=b.line_start,
                line_end=b.line_end,
                heading_hierarchy=heading,
                content=content,
                token_count=tc,
                parent_id=None,
                system=system,
            )
        )
    return out
