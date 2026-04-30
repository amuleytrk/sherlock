"""Haiku-4.5 intent classifier + entity extractor.

The router decides whether a user message goes to the Discovery agent (RAG)
or the RCA agent (filesystem-as-context investigation). It also extracts
structured entities the downstream agent needs (tape_id, env, service, etc.).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from anthropic import Anthropic

from apps.api.settings import get_settings


_PROMPT_PATH = Path(__file__).parent / "prompts" / "router_system.md"
_VALID_INTENTS = {"API_DISCOVERY", "DEBUGGING", "CONVERSATIONAL"}


@dataclass
class RouterResult:
    intent: str
    entities: dict
    raw: str = ""


def _system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _heuristic_classify(message: str) -> RouterResult:
    """Cheap regex-based fallback when no LLM key is available.

    This is intentionally conservative — it's not as accurate as Haiku but it
    keeps the app functional in dev/test without burning API tokens.
    """
    msg = message.lower()
    tape_id = None
    m = re.search(r"\b([0-9A-Fa-f]{12})\b", message)
    if m:
        tape_id = m.group(1).upper()

    service = None
    for s in (
        "ingress-service", "event-preprocessor-service", "device-management-service",
        "external-service", "location-preprocessor", "health-service",
        "healthcare-service", "util-service", "messaging-service",
        "airline-service", "rule-engine", "auth-service", "configuration-service",
        "ag-grid-service", "dashboard-service", "monitoring-service",
        "analytic-service", "webhooks-service", "here-service", "flight-trace",
    ):
        if s in msg:
            service = s
            break

    env = "ppe"
    if re.search(r"\bstage\b|\bstaging\b|\bdev\b", msg):
        env = "stage"
    elif re.search(r"\bprod\b|\bproduction\b", msg):
        env = "prod"
    elif re.search(r"\bppe\b|pre-prod", msg):
        env = "ppe"

    error_hint = None
    em = re.search(r"\b(\d{3})\b", message)
    if em and em.group(1) in {"400", "401", "403", "404", "409", "500", "502", "503", "504"}:
        error_hint = em.group(1)

    debug_signals = ("not appearing", "not showing", "didn't", "did not",
                     "error", "failed", "failing", "500", "503", "wrong", "missing",
                     "broken", "stuck", "issue", "bug", "investigat")
    discovery_signals = ("does an api", "what api", "how do i", "how to",
                         "what does", "what is", "where is", "where can i",
                         "list of", "which service", "which endpoint", "exist for",
                         "config flag", "feature flag")

    if any(s in msg for s in debug_signals) and (tape_id or "device" in msg or service):
        intent = "DEBUGGING"
    elif any(s in msg for s in discovery_signals):
        intent = "API_DISCOVERY"
    elif tape_id:
        intent = "DEBUGGING"
    elif msg.startswith(("hi", "hello", "hey", "thanks", "thank you")):
        intent = "CONVERSATIONAL"
    else:
        intent = "API_DISCOVERY"  # default — most user messages are knowledge questions

    entities = {
        "tape_id": tape_id,
        "qrcode": None, "asset_barcode": None,
        "customer_id": None, "authorized_group": None, "application_id": None,
        "service": service,
        "env": env,
        "feature_flag": None,
        "endpoint": None,
        "error_hint": error_hint,
        "time_window": None,
    }
    return RouterResult(intent=intent, entities=entities, raw="(heuristic)")


def classify(message: str) -> RouterResult:
    """Classify a user message. Uses Haiku 4.5 if ANTHROPIC_API_KEY is set,
    otherwise falls back to a regex heuristic so the app stays functional."""
    s = get_settings()
    if not s.anthropic_api_key:
        return _heuristic_classify(message)

    try:
        client = Anthropic(api_key=s.anthropic_api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=400,
            system=[
                {"type": "text", "text": _system_prompt(), "cache_control": {"type": "ephemeral"}},
            ],
            messages=[{"role": "user", "content": message}],
        )
        text = "".join(b.text for b in resp.content if hasattr(b, "text"))
    except Exception:
        # If the API call fails for any reason, fall back to heuristic
        return _heuristic_classify(message)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        data = json.loads(m.group(0)) if m else None
        if data is None:
            return _heuristic_classify(message)

    intent = data.get("intent", "CONVERSATIONAL")
    if intent not in _VALID_INTENTS:
        intent = "CONVERSATIONAL"
    entities = data.get("entities") or {}
    # Normalize tape_id to uppercase — MSSQL `trk` schema and Cosmos
    # partition keys are uppercase, so downstream tools can compare without
    # case-folding.
    tid = entities.get("tape_id")
    if isinstance(tid, str):
        entities["tape_id"] = tid.upper()
    return RouterResult(intent=intent, entities=entities, raw=text)
