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
