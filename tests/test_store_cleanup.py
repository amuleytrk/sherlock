"""Tests for the cascade-delete and ephemeral-flush behaviors in store.py."""
from __future__ import annotations

from pathlib import Path

import pytest

from apps.api import store
from apps.api.settings import get_settings


@pytest.fixture(autouse=True)
def isolated_db_and_inv(tmp_path, monkeypatch):
    """Point store + investigations dir at tmp paths so each test is isolated."""
    monkeypatch.setenv("SHERLOCK_DB_PATH", str(tmp_path / "sherlock.db"))
    monkeypatch.setenv("SHERLOCK_INVESTIGATIONS_DIR", str(tmp_path / "inv"))
    # Force settings to re-read from env
    import apps.api.settings as settings_mod
    settings_mod._settings = None
    yield
    settings_mod._settings = None


def _make_session_with_rca(session_id: str, rca_id: str) -> None:
    """Insert a session, a user message, an agent message linked to an RCA,
    plus an audit log row keyed by both session_id and rca_id. Also drop
    placeholder files into the rca scratch dir."""
    store.upsert_session(session_id, title=f"sess-{session_id}")
    store.append_message(session_id, role="user", content="bug?")
    store.append_message(session_id, role="agent", content="", rca_id=rca_id)
    inv_dir = get_settings().sherlock_investigations_dir / rca_id
    inv_dir.mkdir(parents=True, exist_ok=True)
    (inv_dir / "final-rca.md").write_text("# example")
    (inv_dir / "evidence").mkdir(exist_ok=True)
    (inv_dir / "evidence" / "001.json").write_text("[]")
    # Insert an audit row referencing this rca_id
    with store.conn() as c:
        c.execute(
            "INSERT INTO audit_log (session_id, rca_id, tool_name, args_json, "
            "outcome, duration_ms, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, rca_id, "trk_kubectl_logs", "{}", "ok", 100, store.now()),
        )


def test_delete_session_cascades_messages_audit_and_rca_dir():
    _make_session_with_rca("sess-A", "rca_aabbccdd")
    inv_dir = get_settings().sherlock_investigations_dir / "rca_aabbccdd"
    assert inv_dir.is_dir()

    summary = store.delete_session("sess-A")

    assert summary["sessions_deleted"] == 1
    assert summary["messages_deleted"] == 2
    assert summary["audit_entries_deleted"] >= 1
    assert summary["rca_dirs_deleted"] == 1
    assert not inv_dir.exists(), "investigation scratch dir should be removed"
    assert store.get_session("sess-A") is None


def test_delete_session_leaves_other_sessions_intact():
    _make_session_with_rca("sess-A", "rca_11111111")
    _make_session_with_rca("sess-B", "rca_22222222")

    store.delete_session("sess-A")

    assert store.get_session("sess-A") is None
    assert store.get_session("sess-B") is not None
    other_dir = get_settings().sherlock_investigations_dir / "rca_22222222"
    assert other_dir.is_dir(), "deleting sess-A must not touch sess-B's scratch dir"


def test_delete_all_sessions_wipes_everything_in_db_and_fs():
    _make_session_with_rca("sess-A", "rca_aaaaaaaa")
    _make_session_with_rca("sess-B", "rca_bbbbbbbb")

    summary = store.delete_all_sessions()

    assert summary["sessions_deleted"] == 2
    assert summary["messages_deleted"] == 4
    assert summary["audit_entries_deleted"] >= 2

    inv_root = get_settings().sherlock_investigations_dir
    remaining = [c.name for c in inv_root.iterdir() if c.is_dir()] if inv_root.exists() else []
    assert remaining == [], f"all rca scratch dirs should be gone, found {remaining}"


def test_delete_all_sweeps_orphaned_scratch_dirs():
    """Even rca dirs without a corresponding DB row get cleaned (e.g. left
    over from a crash mid-persist)."""
    inv_root = get_settings().sherlock_investigations_dir
    inv_root.mkdir(parents=True, exist_ok=True)
    orphan = inv_root / "rca_deadbeef"
    orphan.mkdir()
    (orphan / "evidence").mkdir()
    (orphan / "evidence" / "x.txt").write_text("orphan")

    store.delete_all_sessions()

    assert not orphan.exists(), "orphaned scratch dir should be swept on clear-all"


def test_delete_all_refuses_to_touch_non_rca_paths():
    """Sibling dirs in investigations/ that don't match the rca_<hex8>
    pattern must NOT be deleted (path-traversal / mistaken-name guard)."""
    inv_root = get_settings().sherlock_investigations_dir
    inv_root.mkdir(parents=True, exist_ok=True)
    safe = inv_root / "user-keepsake"
    safe.mkdir()
    (safe / "important.md").write_text("do not delete")

    store.delete_all_sessions()

    assert safe.is_dir()
    assert (safe / "important.md").read_text() == "do not delete"


def test_rmtree_rca_dir_rejects_traversal():
    """Direct call with a non-matching path component should be a no-op."""
    inv_root = get_settings().sherlock_investigations_dir
    inv_root.mkdir(parents=True, exist_ok=True)
    target = inv_root.parent / "outside_inv"
    target.mkdir()
    (target / "x").write_text("nope")
    # Even though we're calling the helper directly with bad input, regex
    # rejects anything not matching rca_<hex8>.
    store._rmtree_rca_dir("../outside_inv")
    assert target.is_dir()
    assert (target / "x").read_text() == "nope"
