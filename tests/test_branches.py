"""Tests for indexer.branches.load() and the indexer's branch-validation gate."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from indexer.branches import RepoSpec, load


def test_load_returns_release_and_repos(tmp_path):
    f = tmp_path / "repos.yml"
    f.write_text("""
release: ppe
repos:
  multi-tenant-core-services: release_1.20
  ann-rule-engine: release_2.0
""")
    cfg = load(f)
    assert cfg.release == "ppe"
    assert len(cfg.repos) == 2
    assert cfg.branch_for("multi-tenant-core-services") == "release_1.20"
    assert cfg.branch_for("ann-rule-engine") == "release_2.0"
    assert cfg.branch_for("nonexistent") is None


def test_load_raises_on_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load(tmp_path / "nope.yml")


def test_load_raises_on_empty_repos(tmp_path):
    f = tmp_path / "repos.yml"
    f.write_text("release: ppe\nrepos: {}\n")
    with pytest.raises(ValueError, match="no `repos:` entries"):
        load(f)


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git"] + args, cwd=cwd, capture_output=True, check=True)


def _make_test_repo(path: Path, branches: list[str]) -> None:
    """Create a tmp git repo with named branches at distinct commits."""
    path.mkdir(parents=True)
    _git(["init", "-q", "-b", "main"], cwd=path)
    _git(["config", "user.email", "t@t"], cwd=path)
    _git(["config", "user.name", "test"], cwd=path)
    (path / "README.md").write_text("# initial\n")
    _git(["add", "."], cwd=path)
    _git(["commit", "-q", "-m", "initial"], cwd=path)

    # Add a fake remote so origin/<branch> resolves
    fake_remote = path.parent / f"{path.name}.fake-remote.git"
    _git(["init", "-q", "--bare", str(fake_remote)], cwd=path.parent)
    _git(["remote", "add", "origin", str(fake_remote)], cwd=path)

    for b in branches:
        _git(["checkout", "-q", "-b", b], cwd=path)
        (path / f"{b}.txt").write_text(b)
        _git(["add", "."], cwd=path)
        _git(["commit", "-q", "-m", b], cwd=path)
        _git(["push", "-q", "origin", b], cwd=path)
    _git(["checkout", "-q", "main"], cwd=path)


def test_validate_passes_when_repo_on_expected_branch(tmp_path, monkeypatch):
    repos_dir = tmp_path / "repos"
    repos_dir.mkdir()
    repo_path = repos_dir / "fake-repo"
    _make_test_repo(repo_path, ["release_1.20"])
    # checkout the right branch (detached, like a worktree would)
    _git(["checkout", "-q", "--detach", "origin/release_1.20"], cwd=repo_path)

    yml = tmp_path / "repos.yml"
    yml.write_text("release: ppe\nrepos:\n  fake-repo: release_1.20\n")
    monkeypatch.setenv("SHERLOCK_REPOS_YML", str(yml))

    from indexer.run import _validate_repos_on_correct_branches
    _validate_repos_on_correct_branches(repos_dir)


def test_validate_raises_when_repo_on_wrong_branch(tmp_path, monkeypatch):
    repos_dir = tmp_path / "repos"
    repos_dir.mkdir()
    repo_path = repos_dir / "fake-repo"
    _make_test_repo(repo_path, ["release_1.20", "release_1.21"])
    _git(["checkout", "-q", "--detach", "origin/release_1.21"], cwd=repo_path)

    yml = tmp_path / "repos.yml"
    yml.write_text("release: ppe\nrepos:\n  fake-repo: release_1.20\n")
    monkeypatch.setenv("SHERLOCK_REPOS_YML", str(yml))

    from indexer.run import _validate_repos_on_correct_branches
    with pytest.raises(RuntimeError, match="not on the branch declared in repos.yml"):
        _validate_repos_on_correct_branches(repos_dir)


def test_validate_raises_when_repo_missing(tmp_path, monkeypatch):
    repos_dir = tmp_path / "repos"
    repos_dir.mkdir()

    yml = tmp_path / "repos.yml"
    yml.write_text("release: ppe\nrepos:\n  missing-repo: release_1.20\n")
    monkeypatch.setenv("SHERLOCK_REPOS_YML", str(yml))

    from indexer.run import _validate_repos_on_correct_branches
    with pytest.raises(RuntimeError, match="missing at"):
        _validate_repos_on_correct_branches(repos_dir)
