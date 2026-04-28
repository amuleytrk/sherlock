"""Prepare repos/ for indexing using git worktrees.

For each repo in `repos.yml`:

1. Find a source clone:
   - Prefer ~/Documents/repository/<name> if it's a git repo (so we can
     reuse the user's existing git objects).
   - Otherwise clone fresh into ~/Documents/repository/sherlock/.git-cache/<name>
     so worktrees have something to point at.

2. `git fetch origin <branch>` in the source so the ref is local.

3. Remove any existing entry at repos/<name> (symlink, worktree, or stale dir).

4. Create a fresh worktree at repos/<name> at `origin/<branch>` (detached
   HEAD — we never commit from the worktree, just read).

The user's main working copy stays untouched throughout.

Usage: `uv run python -m scripts.prepare_repos`
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from indexer.branches import RepoSpec, load


SHERLOCK_ROOT = Path(__file__).parent.parent.resolve()
REPOS_DIR = SHERLOCK_ROOT / "repos"
GIT_CACHE_DIR = SHERLOCK_ROOT / ".git-cache"
USER_REPO_PARENT = Path.home() / "Documents" / "repository"

GH_OWNER = "Trackonomy"


def _run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> tuple[int, str, str]:
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and res.returncode != 0:
        raise RuntimeError(
            f"command failed: {' '.join(cmd)}\n"
            f"  cwd: {cwd}\n  stdout: {res.stdout}\n  stderr: {res.stderr}"
        )
    return res.returncode, res.stdout, res.stderr


def _is_git_repo(path: Path) -> bool:
    if not path.is_dir():
        return False
    rc, _, _ = _run(["git", "rev-parse", "--is-inside-work-tree"], cwd=path, check=False)
    return rc == 0


def _ensure_source_clone(repo: RepoSpec) -> Path:
    """Return the path to a usable source clone — either the user's existing
    repo, or a sherlock-managed cache clone."""
    user_clone = USER_REPO_PARENT / repo.name
    if _is_git_repo(user_clone):
        print(f"  source: user's working copy at {user_clone}")
        return user_clone

    cache_clone = GIT_CACHE_DIR / repo.name
    if _is_git_repo(cache_clone):
        print(f"  source: sherlock cache at {cache_clone}")
        return cache_clone

    GIT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"  source: cloning {GH_OWNER}/{repo.name} into {cache_clone}")
    rc, out, err = _run(
        ["gh", "repo", "clone", f"{GH_OWNER}/{repo.name}", str(cache_clone), "--"],
        check=False,
    )
    if rc != 0:
        raise RuntimeError(f"failed to gh clone {repo.name}: {err}")
    return cache_clone


def _remove_existing(target: Path) -> None:
    """Remove whatever's at target — symlink, dir, or stale worktree registration."""
    if target.is_symlink() or target.exists():
        # If it's a registered worktree, ask git to remove it cleanly first.
        # Look up the source repo by walking up.
        try:
            rc, out, _ = _run(
                ["git", "rev-parse", "--git-dir"], cwd=target, check=False
            )
            if rc == 0 and out.strip():
                # Find the main repo
                rc2, main, _ = _run(
                    ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                    cwd=target, check=False,
                )
                if rc2 == 0:
                    main_dir = Path(main.strip()).parent
                    _run(
                        ["git", "worktree", "remove", "--force", str(target)],
                        cwd=main_dir, check=False,
                    )
        except Exception:
            pass
        if target.is_symlink():
            target.unlink()
        elif target.exists():
            shutil.rmtree(target)


def _fetch_branch(source: Path, branch: str) -> None:
    print(f"  fetching origin/{branch}…")
    rc, out, err = _run(
        ["git", "fetch", "origin", branch], cwd=source, check=False
    )
    if rc != 0:
        raise RuntimeError(
            f"git fetch failed for {branch} in {source} — does the branch exist?\n"
            f"stderr: {err}"
        )


def _add_worktree(source: Path, target: Path, branch: str) -> None:
    print(f"  worktree → {target} @ origin/{branch}")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Detached HEAD: we just want the files at that ref. No local branch.
    _run(
        ["git", "worktree", "add", "--detach", "--force", str(target), f"origin/{branch}"],
        cwd=source,
    )


def _verify(target: Path, expected_branch: str) -> None:
    rc, out, _ = _run(["git", "rev-parse", "HEAD"], cwd=target)
    head = out.strip()[:8]
    rc, expected, _ = _run(
        ["git", "rev-parse", f"origin/{expected_branch}"], cwd=target
    )
    expected = expected.strip()[:8]
    if head != expected:
        raise RuntimeError(
            f"verification failed for {target}: HEAD={head}, expected={expected} (origin/{expected_branch})"
        )
    print(f"  ✓ verified — HEAD {head} matches origin/{expected_branch}")


def main():
    cfg = load()
    print(f"Preparing repos/ for release tag '{cfg.release}'")
    print(f"  source-of-truth: repos.yml ({len(cfg.repos)} repos)")
    print()

    REPOS_DIR.mkdir(exist_ok=True)
    failures = []

    for repo in cfg.repos:
        target = REPOS_DIR / repo.name
        print(f"→ {repo.name} @ {repo.branch}")
        try:
            source = _ensure_source_clone(repo)
            _fetch_branch(source, repo.branch)
            _remove_existing(target)
            _add_worktree(source, target, repo.branch)
            _verify(target, repo.branch)
        except Exception as e:
            print(f"  ✗ FAILED: {e}")
            failures.append((repo.name, str(e)))
        print()

    if failures:
        print(f"⚠ {len(failures)} repo(s) failed:")
        for name, err in failures:
            print(f"  - {name}: {err}")
        sys.exit(1)

    print("✓ all repos prepared.")
    print()
    print("Verify with:  ls -la repos/")
    print("Then index:    uv run python -m indexer.run")


if __name__ == "__main__":
    main()
