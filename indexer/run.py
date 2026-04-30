"""Indexer CLI: `python -m indexer.run [--file PATH | --limit N]`.

Day-1: index a single markdown file via --file.
Day-2: full-corpus mode (5 repos + ~/plans/work) when no --file.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import subprocess

from apps.api.settings import get_settings
from indexer.branches import load as load_branch_config
from indexer.chunk import chunk_code, chunk_markdown
from indexer.crawl import classify_file, walk_corpus
from indexer.embed import upsert_chunks
from indexer.parse import parse_markdown
from indexer.secret_scan import contains_secret


# Repos that hold many micro-services as subdirectories. For these, tag chunks
# with the SECOND path component (the actual service / package), not the repo
# name — so RCA agents can filter `sherlock_search(service="ingress-service")`
# and actually get results. Files at the monorepo root keep the repo name.
_MONOREPOS: set[str] = {"multi-tenant-core-services"}


def _service_for(path: Path, repos_root: Path) -> str:
    """Determine the service tag for a chunk based on its path.

    - Files outside `repos/` (e.g. ~/plans/work) → `platform`.
    - Files under `repos/<single-service-repo>/...` → repo name.
    - Files under `repos/<monorepo>/<sub>/...` → sub-service name.
    - Files at a monorepo root (README.md, package.json) → repo name.
    """
    try:
        rel = path.resolve().relative_to(repos_root.resolve())
    except ValueError:
        return "platform"
    parts = rel.parts
    if not parts:
        return "platform"
    repo = parts[0]
    if repo in _MONOREPOS and len(parts) >= 3:
        # parts[1] is the sub-service / package; parts[2..] is the file inside.
        return parts[1]
    return repo


def _validate_repos_on_correct_branches(repos_root: Path) -> None:
    """Before indexing, fail loudly if any repo isn't on the branch declared
    in repos.yml. Prevents silently indexing the wrong code (e.g. a feature
    branch instead of release_1.20)."""
    cfg = load_branch_config()
    repos_root = repos_root.resolve()
    failures = []
    for repo in cfg.repos:
        repo_path = repos_root / repo.name
        if not repo_path.exists():
            failures.append(f"  - {repo.name}: missing at {repo_path} — run `uv run python -m scripts.prepare_repos`")
            continue
        try:
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_path, capture_output=True, text=True, check=True,
            ).stdout.strip()
            expected = subprocess.run(
                ["git", "rev-parse", f"origin/{repo.branch}"],
                cwd=repo_path, capture_output=True, text=True, check=True,
            ).stdout.strip()
        except subprocess.CalledProcessError as e:
            failures.append(f"  - {repo.name}: git rev-parse failed: {e.stderr.strip()}")
            continue
        if head != expected:
            failures.append(
                f"  - {repo.name}: HEAD={head[:8]} but expected origin/{repo.branch}={expected[:8]}\n"
                f"      → run `uv run python -m scripts.prepare_repos` to fix"
            )
    if failures:
        msg = (
            "Indexer aborted — one or more repos are not on the branch declared in repos.yml.\n"
            "This means we'd be indexing the WRONG code (e.g. a feature branch instead of a release branch).\n\n"
            + "\n".join(failures)
        )
        raise RuntimeError(msg)


def index_path(path: Path, release: str, repos_root: Path) -> int:
    cat = classify_file(path)
    if cat is None:
        return 0
    service = _service_for(path, repos_root)

    text = path.read_text(encoding="utf-8", errors="ignore")

    if path.suffix == ".md":
        blocks = parse_markdown(text, file_path=str(path))
        chunks = chunk_markdown(blocks, release=release, service=service, category=cat)
    elif path.suffix in {".js", ".jsx", ".ts", ".tsx"}:
        # Code parser lands in Day 2 (parse_code.py). Until then, skip code files.
        try:
            from indexer.parse_code import parse_code_file
        except ImportError:
            return 0
        blocks = parse_code_file(path)
        chunks = chunk_code(blocks, release=release, service=service, category=cat)
    else:
        return 0

    chunks = [c for c in chunks if not contains_secret(c.content)]

    # If contains_secret filtered out a parent chunk, its children would fail
    # the chunks_parent_id_fkey constraint at upsert time. Nullify any
    # parent_id reference pointing at a chunk that no longer survives.
    surviving_ids = {c.chunk_id for c in chunks}
    for c in chunks:
        if c.parent_id and c.parent_id not in surviving_ids:
            c.parent_id = None

    return upsert_chunks(chunks, verbose=False)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--release", default=None, help="Release tag; defaults to settings")
    p.add_argument("--file", type=Path, default=None, help="Index a single file (smoke mode)")
    p.add_argument("--limit", type=int, default=None, help="Cap on files indexed")
    p.add_argument("--root", type=Path, action="append", help="Add an additional root to crawl")
    args = p.parse_args()

    s = get_settings()
    release = args.release or s.sherlock_release

    if args.file:
        if not args.file.is_file():
            print(f"not a file: {args.file}", file=sys.stderr)
            sys.exit(1)
        n = index_path(args.file, release, s.sherlock_repos_dir)
        print(f"indexed {n} chunks from {args.file}")
        return

    # Full corpus mode: hard-fail if any repo is on the wrong branch.
    if s.sherlock_repos_dir.exists():
        _validate_repos_on_correct_branches(s.sherlock_repos_dir)

    roots = args.root or []
    if s.sherlock_repos_dir.exists():
        roots.append(s.sherlock_repos_dir)
    plans_root = Path.home() / "plans" / "work"
    if plans_root.exists():
        roots.append(plans_root)

    if not roots:
        print("no roots to crawl. use --root, --file, or populate ./repos/", file=sys.stderr)
        sys.exit(1)

    total_chunks = 0
    files_seen = 0
    for path in walk_corpus(roots):
        if args.limit and files_seen >= args.limit:
            break
        n = index_path(path, release, s.sherlock_repos_dir)
        if n:
            print(f"  +{n:3d} from {path}")
            total_chunks += n
        files_seen += 1
    print(f"\nTOTAL: {total_chunks} chunks from {files_seen} files")


if __name__ == "__main__":
    main()
