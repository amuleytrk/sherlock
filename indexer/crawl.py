"""Walk the corpus filesystem and classify each file by category."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator


CATEGORY_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r".*/routes/.*\.(js|ts)$"), "api_route"),
    (re.compile(r".*/controllers/.*\.(js|ts)$"), "controller"),
    (re.compile(r".*/CLAUDE\.md$"), "service_architecture"),
    (re.compile(r"(^|/)systemFlow\.md$"), "architecture"),
    (re.compile(r"(^|/)pgSystemFlow\.md$"), "architecture"),
    (re.compile(r".*/changelog/.*\.up\.sql$"), "ddl"),
    (re.compile(r".*/constants\.(js|ts)$"), "config"),
    (re.compile(r".*/eventSchema\.(js|ts)$"), "validation"),
    (re.compile(r".*/middleware/.*\.(js|ts)$"), "validation"),
    (re.compile(r".*/repository/.*\.(js|ts)$"), "data_access"),
    (re.compile(r".*/helpers/.*\.(js|ts)$"), "helper"),
    (re.compile(r".*/components/.*\.(jsx?|tsx?)$"), "frontend_component"),
    (re.compile(r".*/(slice|store|reducer)s?/.*\.(jsx?|tsx?)$"), "frontend_state"),
    (re.compile(r"/plans/work/.*\.md$"), "planning_doc"),
    (re.compile(r".*/README\.md$"), "documentation"),
]


EXCLUDE_DIR_NAMES = {
    "node_modules", "dist", "build", ".git", ".next", ".cache",
    ".obsidian", ".trash", "__pycache__", ".venv", "venv",
    "_archive", "_drafts", "_scratch", "_inbox",
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
}

EXCLUDE_SUFFIXES = {
    ".canvas", ".lock", ".log",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp",
    ".woff", ".woff2", ".ttf", ".eot",
    ".pdf", ".zip", ".tar", ".gz", ".bz2",
    ".mp4", ".mov", ".avi",
}

INDEXABLE_SUFFIXES = {".md", ".js", ".jsx", ".ts", ".tsx", ".sql", ".json"}


def classify_file(path: Path) -> str | None:
    """Return the category for a file path, or None if it should be skipped."""
    parts = path.parts
    if any(part.startswith(".") for part in parts):
        return None
    if path.name.startswith("."):
        return None
    if path.suffix in EXCLUDE_SUFFIXES:
        return None

    s = str(path)
    for pattern, category in CATEGORY_RULES:
        if pattern.search(s):
            return category

    if path.suffix in {".js", ".ts", ".jsx", ".tsx", ".sql"}:
        return "code_other"
    if path.suffix == ".md":
        return "documentation"
    return None


def walk_corpus(roots: list[Path]) -> Iterator[Path]:
    """Yield indexable files under each root, skipping excluded dirs."""
    for root in roots:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in EXCLUDE_DIR_NAMES for part in path.parts):
                continue
            if classify_file(path) is None:
                continue
            yield path
