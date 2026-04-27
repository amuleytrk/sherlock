"""Parse markdown into heading-scoped blocks with parent/child links.

Each `##` / `###` / etc. heading starts a new block. The block's content is
the heading line plus everything until the next heading at the same or
higher level. `parent_heading` points to the immediate ancestor at one
level up, enabling the indexer to link parent/child chunks for retrieval.
"""
from __future__ import annotations

from dataclasses import dataclass

from markdown_it import MarkdownIt


@dataclass
class MarkdownBlock:
    file_path: str
    heading_hierarchy: list[str]
    content: str
    line_start: int
    line_end: int
    parent_heading: str | None = None


def parse_markdown(text: str, file_path: str) -> list[MarkdownBlock]:
    md = MarkdownIt("commonmark")
    tokens = md.parse(text)
    lines = text.splitlines()

    # First pass: find heading positions and build hierarchy
    headings: list[tuple[int, int, str]] = []  # (line_index_0, level, title)
    for i, tok in enumerate(tokens):
        if tok.type == "heading_open" and tok.map:
            level = int(tok.tag[1])
            title = tokens[i + 1].content.strip()
            headings.append((tok.map[0], level, title))

    if not headings:
        return []

    blocks: list[MarkdownBlock] = []
    stack: list[tuple[int, str]] = []  # (level, title)

    for idx, (line_zero, level, title) in enumerate(headings):
        # Pop stack entries with level >= current
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))

        line_start = line_zero + 1
        # End at the next heading's line (exclusive), or end of file
        line_end = headings[idx + 1][0] if idx + 1 < len(headings) else len(lines)

        content_lines = lines[line_zero:line_end]
        content = "\n".join(content_lines).strip()

        hierarchy = [t for _, t in stack]
        parent = hierarchy[-2] if len(hierarchy) > 1 else None

        blocks.append(
            MarkdownBlock(
                file_path=file_path,
                heading_hierarchy=hierarchy,
                content=content,
                line_start=line_start,
                line_end=line_end,
                parent_heading=parent,
            )
        )

    return blocks
