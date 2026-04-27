"""Tests for indexer/chunk.py — converting MarkdownBlocks to chunks."""
from __future__ import annotations

from indexer.chunk import Chunk, chunk_id_for, chunk_markdown
from indexer.parse import MarkdownBlock


def _block(h: list[str], content: str, ls: int = 1, le: int = 10, parent: str | None = None) -> MarkdownBlock:
    return MarkdownBlock(
        file_path="x.md",
        heading_hierarchy=h,
        content=content,
        line_start=ls,
        line_end=le,
        parent_heading=parent,
    )


def test_chunk_assigns_deterministic_ids():
    block = _block(["A"], "hello world", 1, 5)
    chunks = chunk_markdown([block], release="ppe")
    assert len(chunks) == 1
    assert chunks[0].chunk_id == chunk_id_for("x.md", 1, 5, "ppe")


def test_chunk_links_parent_child():
    parent = _block(["A"], "parent content", 1, 5)
    child = _block(["A", "A.1"], "child content", 6, 10, parent="A")
    chunks = chunk_markdown([parent, child], release="ppe")
    p = next(c for c in chunks if c.heading_hierarchy == ["A"])
    c = next(c for c in chunks if c.heading_hierarchy == ["A", "A.1"])
    assert c.parent_id == p.chunk_id


def test_oversized_chunk_split():
    big = _block(["Big"], "para1.\n\n" + ("x " * 4000) + "\n\npara3.", 1, 100)
    chunks = chunk_markdown([big], release="ppe", max_tokens=500)
    assert len(chunks) >= 2
    for c in chunks:
        assert c.token_count <= 700


def test_chunk_carries_service_and_category():
    block = _block(["A"], "content", 1, 5)
    chunks = chunk_markdown([block], release="ppe", service="ingress-service", category="architecture")
    assert chunks[0].service == "ingress-service"
    assert chunks[0].category == "architecture"
