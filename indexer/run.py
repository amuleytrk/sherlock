"""Indexer CLI: `python -m indexer.run [--file PATH | --limit N]`.

Day-1: index a single markdown file via --file.
Day-2: full-corpus mode (5 repos + ~/plans/work) when no --file.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from apps.api.settings import get_settings
from indexer.chunk import chunk_code, chunk_markdown
from indexer.crawl import classify_file, walk_corpus
from indexer.embed import upsert_chunks
from indexer.parse import parse_markdown
from indexer.secret_scan import contains_secret


def _service_for(path: Path, repos_root: Path) -> str:
    """The first path component under `repos/` is the service name. Files outside
    `repos/` (e.g. ~/plans/work) get `service='platform'`."""
    try:
        rel = path.resolve().relative_to(repos_root.resolve())
        return rel.parts[0] if rel.parts else "platform"
    except ValueError:
        return "platform"


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
