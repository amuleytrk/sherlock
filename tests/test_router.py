"""Router tests. The non-LLM heuristic path is tested directly; the live LLM
path is tested in tests/live/."""
from __future__ import annotations

from apps.api.router import RouterResult, _heuristic_classify, classify


def test_heuristic_returns_dataclass():
    out = _heuristic_classify("Hi there")
    assert isinstance(out, RouterResult)


def test_heuristic_classifies_api_discovery_question():
    out = _heuristic_classify("How do I use the device labelling API?")
    assert out.intent == "API_DISCOVERY"


def test_heuristic_classifies_debugging_with_tape_id():
    out = _heuristic_classify("Device AABBCCDDEEFF events not appearing in lookup_parcels in PPE")
    assert out.intent == "DEBUGGING"
    assert out.entities["tape_id"] == "AABBCCDDEEFF"
    assert out.entities["env"] == "ppe"


def test_heuristic_default_env_is_ppe():
    out = _heuristic_classify("How do I label a device?")
    assert out.entities["env"] == "ppe"


def test_heuristic_extracts_service():
    out = _heuristic_classify("Why is ingress-service throwing 500?")
    assert out.entities["service"] == "ingress-service"
    assert out.entities["error_hint"] == "500"


def test_heuristic_classifies_greeting_as_conversational():
    out = _heuristic_classify("Hi, what can you help me with?")
    assert out.intent == "CONVERSATIONAL"


def test_heuristic_classifies_feature_flag_question():
    out = _heuristic_classify("What does feature_configuration.cross_customer_mesh_allowed control?")
    assert out.intent == "API_DISCOVERY"


def test_classify_falls_back_to_heuristic_when_no_api_key(monkeypatch):
    """When ANTHROPIC_API_KEY is empty, classify() should use the heuristic
    path. We stub get_settings to fake an empty key — robust against the
    developer's local .env having a real key populated."""
    class _FakeSettings:
        anthropic_api_key = ""

    monkeypatch.setattr("apps.api.router.get_settings", lambda: _FakeSettings())

    out = classify("Where is the lime selection algorithm implemented?")
    assert out.raw == "(heuristic)"
    assert out.intent == "API_DISCOVERY"
