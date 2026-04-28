"""Read repos.yml — the source-of-truth for which branch each repo is on.

Both the corpus-prep script and the indexer use this to (a) check out the
right branch, and (b) validate that whatever's actually on disk matches
expectations before embedding.

Override the path via env var `SHERLOCK_REPOS_YML` (mostly for tests).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


REPOS_YML = Path(__file__).parent.parent / "repos.yml"


def _resolve_path(path: Path | None) -> Path:
    if path is not None:
        return path
    override = os.environ.get("SHERLOCK_REPOS_YML")
    if override:
        return Path(override)
    return REPOS_YML


@dataclass(frozen=True)
class RepoSpec:
    name: str
    branch: str


@dataclass(frozen=True)
class CorpusConfig:
    release: str
    repos: tuple[RepoSpec, ...]

    def branch_for(self, repo_name: str) -> str | None:
        for r in self.repos:
            if r.name == repo_name:
                return r.branch
        return None

    def repo_names(self) -> tuple[str, ...]:
        return tuple(r.name for r in self.repos)


def load(path: Path | None = None) -> CorpusConfig:
    path = _resolve_path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Create it (see the existing repos.yml in this repo for the schema)."
        )
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must be a mapping at the top level")
    release = raw.get("release", "ppe")
    repos_raw = raw.get("repos") or {}
    repos = tuple(RepoSpec(name=k, branch=v) for k, v in repos_raw.items())
    if not repos:
        raise ValueError(f"{path} has no `repos:` entries")
    return CorpusConfig(release=release, repos=repos)
