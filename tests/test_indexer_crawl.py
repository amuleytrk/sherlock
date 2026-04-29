"""Tests for indexer/crawl.py — file classification + walking."""
from __future__ import annotations

from pathlib import Path

from indexer.crawl import classify_file, walk_corpus


def test_classify_routes_file():
    assert classify_file(Path("ingress-service/routes/proxRoutes.js")) == "api_route"


def test_classify_controller_file():
    assert classify_file(Path("ingress-service/controllers/IngressController.js")) == "controller"


def test_classify_systemflow_md():
    assert classify_file(Path("systemFlow.md")) == "architecture"


def test_classify_pgsystemflow_md():
    assert classify_file(Path("pgSystemFlow.md")) == "architecture"


def test_classify_service_claude_md():
    assert classify_file(Path("ingress-service/CLAUDE.md")) == "service_architecture"


def test_classify_planning_doc():
    assert classify_file(Path("/Users/x/plans/work/designs/foo/bar.md")) == "planning_doc"


def test_classify_skips_dotdir():
    assert classify_file(Path(".obsidian/config.json")) is None


def test_classify_skips_canvas():
    assert classify_file(Path("notes.canvas")) is None


def test_classify_skips_image():
    assert classify_file(Path("docs/screenshot.png")) is None


def test_walk_corpus_excludes_node_modules(tmp_path: Path):
    (tmp_path / "service-a" / "node_modules" / "foo").mkdir(parents=True)
    (tmp_path / "service-a" / "node_modules" / "foo" / "x.js").write_text("// nope")
    (tmp_path / "service-a" / "controllers").mkdir(parents=True)
    (tmp_path / "service-a" / "controllers" / "ctl.js").write_text("// yes")

    files = list(walk_corpus([tmp_path]))
    paths = [str(f) for f in files]

    assert any("controllers/ctl.js" in p for p in paths)
    assert not any("node_modules" in p for p in paths)


def test_walk_corpus_excludes_dist_and_dotgit(tmp_path: Path):
    (tmp_path / ".git" / "refs").mkdir(parents=True)
    (tmp_path / ".git" / "refs" / "x").write_text("oid")
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "bundle.js").write_text("// dist")
    (tmp_path / "src" / "controllers").mkdir(parents=True)
    (tmp_path / "src" / "controllers" / "y.js").write_text("// yes")

    files = list(walk_corpus([tmp_path]))
    paths = [str(f) for f in files]

    assert any("controllers/y.js" in p for p in paths)
    # The test func name itself contains "dist", which appears in tmp_path on
    # some platforms. Match the dir we care about more precisely.
    assert not any("/dist/" in p or p.endswith("/dist") for p in paths)
    assert not any("/.git/" in p for p in paths)


def test_walk_corpus_excludes_path_prefixes(tmp_path: Path, monkeypatch):
    """Path-based exclusions (e.g. ~/plans/work/logs and the rca-tool
    design dir) should skip any file under the excluded path."""
    from indexer import crawl as crawl_mod

    # Create a fake plans/work tree
    plans = tmp_path / "plans" / "work"
    (plans / "designs" / "platform").mkdir(parents=True)
    (plans / "designs" / "platform" / "systemFlow.md").write_text("# system flow")

    (plans / "designs" / "rca-tool").mkdir(parents=True)
    (plans / "designs" / "rca-tool" / "sherlock-design.md").write_text("# self-ref")
    (plans / "designs" / "rca-tool" / "implementation").mkdir()
    (plans / "designs" / "rca-tool" / "implementation" / "day-1.md").write_text("# day 1")

    (plans / "logs").mkdir()
    (plans / "logs" / "raw-trace.md").write_text("# log dump")

    (plans / "customer-docs").mkdir()
    (plans / "customer-docs" / "delta.md").write_text("# delta info")

    monkeypatch.setattr(crawl_mod, "EXCLUDE_PATH_PREFIXES", [
        plans / "logs",
        plans / "designs" / "rca-tool",
    ])

    files = list(crawl_mod.walk_corpus([plans]))
    paths = [str(f) for f in files]

    # Kept: systemFlow.md and customer-docs
    assert any("systemFlow.md" in p for p in paths)
    assert any("customer-docs/delta.md" in p for p in paths)

    # Excluded: anything under logs/ or designs/rca-tool/
    assert not any("/logs/" in p for p in paths)
    assert not any("/rca-tool/" in p for p in paths)
    assert not any("sherlock-design.md" in p for p in paths)
    assert not any("day-1.md" in p for p in paths)


def test_walk_corpus_path_prefix_does_not_match_substring(tmp_path: Path, monkeypatch):
    """A prefix exclusion of `/foo/bar` should not also exclude `/foo/bar-baz/`
    (substring vs path-component match)."""
    from indexer import crawl as crawl_mod

    (tmp_path / "rca-tool").mkdir(parents=True, exist_ok=True)
    (tmp_path / "rca-tool" / "doc.md").write_text("# nope")
    (tmp_path / "rca-tool-extra").mkdir(parents=True, exist_ok=True)
    (tmp_path / "rca-tool-extra" / "doc.md").write_text("# yes please")

    monkeypatch.setattr(crawl_mod, "EXCLUDE_PATH_PREFIXES", [tmp_path / "rca-tool"])

    files = list(crawl_mod.walk_corpus([tmp_path]))
    paths = [str(f) for f in files]

    # rca-tool/doc.md excluded; rca-tool-extra/doc.md kept
    assert any("rca-tool-extra/doc.md" in p for p in paths)
    # The excluded one shouldn't be in the kept list
    assert sum(1 for p in paths if p.endswith("/rca-tool/doc.md")) == 0
