"""Tests for the audit redaction + store wiring."""
from __future__ import annotations

import os
from pathlib import Path

from apps.api.audit import TimedTool, list_for_rca, redact, record
from apps.api import store


def _isolate_db(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SHERLOCK_DB_PATH", str(tmp_path / "test.db"))
    import apps.api.settings as settings_mod
    settings_mod._settings = None


def test_redact_password_assignment():
    out = redact('config={"password": "hunter2supersecret"}')
    assert "hunter2supersecret" not in out
    assert "REDACTED" in out


def test_redact_bearer_token():
    out = redact("Authorization: Bearer abc.def.ghi.jkl.mno")
    assert "Bearer abc.def.ghi.jkl.mno" not in out
    assert "REDACTED" in out


def test_redact_jwt_anywhere():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.foo_bar_baz"
    out = redact(f"got token {jwt} from header")
    assert jwt not in out


def test_redact_aws_access_key():
    out = redact("key=AKIAIOSFODNN7EXAMPLE in config")
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "REDACTED" in out


def test_redact_openai_key():
    out = redact("OPENAI_API_KEY=sk-abc123def456ghi789jkl012mno345pqr")
    assert "sk-abc123def456ghi789jkl012mno345pqr" not in out
    assert "REDACTED" in out


def test_redact_passes_clean_text():
    clean = "device AABBCCDDEEFF events not in lookup_parcels"
    assert redact(clean) == clean


# These test cases come from the literal env var names this app uses. The
# old `\b` regex passed them through unredacted because `\b` doesn't match
# between two word chars (e.g. between `_` and `K` in `DATADOG_API_KEY`).
def test_redact_datadog_api_key_envvar_form():
    out = redact("DATADOG_API_KEY=abcdef0123456789-secret")
    assert "abcdef0123456789-secret" not in out
    assert "REDACTED" in out


def test_redact_anthropic_api_key_envvar_form():
    out = redact("ANTHROPIC_API_KEY=sk-ant-this-should-be-redacted-xyz")
    assert "sk-ant-this-should-be-redacted-xyz" not in out


def test_redact_pg_password_envvar_form():
    out = redact("PG_PPE_PASSWORD=hunter2-with-special-$%^chars")
    assert "hunter2-with-special-$%^chars" not in out


def test_redact_cosmos_key_envvar_form():
    # Cosmos keys are long base64-ish strings
    out = redact("COSMOS_PPE_KEY=Q29zbW9zS2V5VGhhdEZha2VkSW5UZXN0RGF0YQ==")
    assert "Q29zbW9zS2V5VGhhdEZha2VkSW5UZXN0RGF0YQ==" not in out


def test_redact_redis_key_envvar_form():
    out = redact("REDIS_PPE_KEY=AbCdEf123456=")
    assert "AbCdEf123456=" not in out


def test_redact_url_embedded_password():
    """rediss://:secretpwd@host should preserve scheme/host but redact password."""
    out = redact("connecting to rediss://:supersecretpwd@redis.example.com:6380")
    assert "supersecretpwd" not in out
    # Scheme and host should be preserved (so audit log is still useful)
    assert "rediss://" in out
    assert "redis.example.com" in out


def test_redact_postgres_url():
    out = redact("DATABASE_URL=postgresql://sherlock:hunter2@localhost:5432/sherlock")
    assert "hunter2" not in out
    # Host preserved
    assert "localhost" in out
    assert "postgresql://" in out


def test_redact_url_without_password_unchanged():
    """A URL with no embedded password should pass through untouched."""
    out = redact("https://api.example.com/path?id=42")
    assert out == "https://api.example.com/path?id=42"


def test_redact_does_not_overmatch_dotted_keyword():
    """A word like 'monkey' should not match 'key'."""
    out = redact("the monkey patches are clean")
    assert out == "the monkey patches are clean"


def test_record_and_list_for_rca(tmp_path, monkeypatch):
    _isolate_db(tmp_path, monkeypatch)
    record(None, "rca_abc", "trk_postgres_query", {"query_type": "device_config", "params": {"device_id": "AABB"}}, "ok", 42)
    record(None, "rca_abc", "trk_kubectl_logs", {"namespace": "trk", "label_selector": "app=ingress"}, "ok", 137)
    entries = list_for_rca("rca_abc")
    assert len(entries) == 2
    assert entries[0]["tool_name"] == "trk_postgres_query"
    assert entries[0]["duration_ms"] == 42


def test_timedtool_records_ok_outcome(tmp_path, monkeypatch):
    _isolate_db(tmp_path, monkeypatch)
    with TimedTool(None, "rca_xyz", "trk_redis_get", {"key_type": "idict"}):
        pass
    entries = list_for_rca("rca_xyz")
    assert len(entries) == 1
    assert entries[0]["outcome"] == "ok"


def test_timedtool_records_error_outcome_and_redacts_args(tmp_path, monkeypatch):
    _isolate_db(tmp_path, monkeypatch)
    try:
        with TimedTool(None, "rca_err", "fake_tool", {"password": "shouldnotpersist"}):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    entries = list_for_rca("rca_err")
    assert len(entries) == 1
    assert entries[0]["outcome"] == "error"
    assert "shouldnotpersist" not in entries[0]["args_json"]


def test_session_upsert_then_list(tmp_path, monkeypatch):
    _isolate_db(tmp_path, monkeypatch)
    store.upsert_session("sess_1", title="Investigation 1")
    store.upsert_session("sess_2", title="Investigation 2")
    sessions = store.list_sessions()
    assert len(sessions) == 2
    assert {s["id"] for s in sessions} == {"sess_1", "sess_2"}
