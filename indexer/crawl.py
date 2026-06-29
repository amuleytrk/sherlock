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


# Specific directory paths to skip — used for user-specific opt-outs that are
# too narrow to live in EXCLUDE_DIR_NAMES (e.g. excluding a folder by exact
# location rather than just by name). Resolved at walk time so tests can
# monkeypatch the list.
EXCLUDE_PATH_PREFIXES: list[Path] = [
    # Raw log dumps under ~/plans/work/logs/ — may contain PII / correlation
    # IDs / device identifiers from production traces. The user opted out.
    Path.home() / "plans" / "work" / "logs",
    # Sherlock's own design / brainstorm / implementation plans. Indexing
    # these creates a recursion loop where the agent searches "how should I
    # do RCA?" and gets back its own design doc.
    Path.home() / "plans" / "work" / "designs" / "rca-tool",
    # Sherlock's own PG-cutover migration docs (spec/plan/grounding) — meta,
    # same recursion concern as rca-tool. Platform PG knowledge comes from
    # pgSystemFlow.md + the n-level docs + the indexed release_2.1 code.
    Path.home() / "plans" / "work" / "sherlock-pg-repoint",
    # ── Post-MSSQL-cutover exclusions (PG-only corpus) ───────────────────────
    # MSSQL-era master design suite (systemFlow.md etc.) — would otherwise be
    # classified 'architecture' and dominate retrieval with MSSQL table names.
    Path.home() / "plans" / "work" / "designs" / "platform",
    # Migration-diff docs: their MSSQL "before" half pollutes a PG-only corpus.
    Path.home() / "plans" / "work" / "designs" / "postgres" / "postgreSqlMigration.md",
    Path.home() / "plans" / "work" / "designs" / "postgres" / "dataMigrationPg.md",
    Path.home() / "plans" / "work" / "designs" / "postgres" / "bleGenericSupportMigration.md",
    Path.home() / "plans" / "work" / "designs" / "postgres" / "mqttMigration.md",
    Path.home() / "plans" / "work" / "designs" / "postgres" / "nLevelAccountAuth.md",  # superseded by n-level playbook
    # MSSQL-era test infra
    Path.home() / "plans" / "work" / "designs" / "testing-suite",
    # MSSQL-era api-specs
    Path.home() / "plans" / "work" / "api-specs" / "assetsByAbcApi.md",
    Path.home() / "plans" / "work" / "api-specs" / "deviceMgmtReadWriteApis.md",
    Path.home() / "plans" / "work" / "api-specs" / "deviceMgmtDeleteApis.md",
    Path.home() / "plans" / "work" / "api-specs" / "proxApi.md",
    Path.home() / "plans" / "work" / "api-specs" / "bleGenericApis.md",
    Path.home() / "plans" / "work" / "api-specs" / "mobileSynclogApi.md",
    Path.home() / "plans" / "work" / "api-specs" / "brinksAuth.md",
    Path.home() / "plans" / "work" / "api-specs" / "brinksAuthProd.md",
    Path.home() / "plans" / "work" / "api-specs" / "brinksMilestone.md",
    Path.home() / "plans" / "work" / "api-specs" / "brinksMilestoneProd.md",
    # Pre-PG releases
    Path.home() / "plans" / "work" / "releases" / "release_1.19.md",
    Path.home() / "plans" / "work" / "releases" / "release_1.20.md",
    Path.home() / "plans" / "work" / "releases" / "release_2.0.md",
    # All RCA incident docs — MSSQL incidents; PG/Flink ones out of Sherlock scope
    Path.home() / "plans" / "work" / "rca",
    # MSSQL / stale user-docs
    Path.home() / "plans" / "work" / "user-docs" / "cargoIqOld.md",
    Path.home() / "plans" / "work" / "user-docs" / "apisToFork2Envs.md",
    Path.home() / "plans" / "work" / "user-docs" / "longTermLogRetention.md",
    Path.home() / "plans" / "work" / "user-docs" / "azureBlobStorageMigration.md",
    Path.home() / "plans" / "work" / "user-docs" / "bleGenericIntegration.md",
    Path.home() / "plans" / "work" / "user-docs" / "inventoryManufacturingFlow.md",
    # MSSQL customer-docs
    Path.home() / "plans" / "work" / "customer-docs" / "brinks.md",
    Path.home() / "plans" / "work" / "customer-docs" / "brinksFlow.md",
    Path.home() / "plans" / "work" / "customer-docs" / "brinksSecurityFlow.md",
    Path.home() / "plans" / "work" / "customer-docs" / "javaTlsCertTrustReport.md",
    # Internal prompt-engineering reviews — not corpus content
    Path.home() / "plans" / "work" / "prompt-reviews",
]

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
    # Resolve once per call so symlinks/trailing-slashes are normalized.
    excluded_prefixes = [p.resolve() for p in EXCLUDE_PATH_PREFIXES]
    for root in roots:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in EXCLUDE_DIR_NAMES for part in path.parts):
                continue
            try:
                resolved = path.resolve()
            except (OSError, ValueError):
                continue
            if any(_is_under(resolved, prefix) for prefix in excluded_prefixes):
                continue
            if classify_file(path) is None:
                continue
            yield path


def _is_under(path: Path, prefix: Path) -> bool:
    """Return True if `path` is the prefix or any descendant of it."""
    try:
        return path.is_relative_to(prefix)
    except (AttributeError, ValueError):
        # Pre-3.9 fallback (we're on 3.13, but defensive)
        try:
            path.relative_to(prefix)
            return True
        except ValueError:
            return False
