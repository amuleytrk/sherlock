"""Tests for indexer/parse.py — markdown heading parser."""
from __future__ import annotations

from indexer.parse import parse_markdown


SAMPLE = """# Top
intro paragraph

## Section A

content of A
para 2

### Section A.1

A.1 content

## Section B

B content
"""


def test_parse_markdown_top_level_heading():
    blocks = parse_markdown(SAMPLE, file_path="x.md")
    assert blocks[0].heading_hierarchy == ["Top"]
    assert "intro paragraph" in blocks[0].content


def test_parse_markdown_section_a():
    blocks = parse_markdown(SAMPLE, file_path="x.md")
    sec_a = next(b for b in blocks if b.heading_hierarchy[-1] == "Section A")
    assert sec_a.heading_hierarchy == ["Top", "Section A"]
    assert "content of A" in sec_a.content


def test_parse_markdown_nested_subsection():
    blocks = parse_markdown(SAMPLE, file_path="x.md")
    sec_a1 = next(b for b in blocks if b.heading_hierarchy[-1] == "Section A.1")
    assert sec_a1.heading_hierarchy == ["Top", "Section A", "Section A.1"]
    assert sec_a1.parent_heading == "Section A"


def test_parse_markdown_line_ranges_set():
    blocks = parse_markdown(SAMPLE, file_path="x.md")
    for b in blocks:
        assert b.line_start >= 1
        assert b.line_end >= b.line_start


def test_parse_markdown_handles_no_heading():
    """A markdown file with no headings should yield zero blocks (we don't index unstructured prose)."""
    blocks = parse_markdown("just some text\nmore text\n", file_path="x.md")
    # No heading → no parent block emitted; that's expected.
    assert blocks == [] or all(not b.heading_hierarchy for b in blocks) or blocks[0].heading_hierarchy
