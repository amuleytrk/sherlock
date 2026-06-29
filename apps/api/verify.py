"""Trust layer: extract factual claims from a Discovery answer and have a
separate Haiku model evaluate whether each is supported by the cited corpus
chunks.

Two-stage:
1. `extract_claims(markdown)` — regex-based pull of high-precision claim shapes:
   - HTTP endpoints (METHOD /path), e.g. ``GET /devices/v3/events/latest``
   - SQL fully-qualified table or column references (``trk.foo``, ``trk.foo.bar``)
     e.g. ``trk.device_event``, ``trk.device``, ``trk.configuration``
   - Feature flags (``feature_configuration.<flag>``), e.g.
     ``feature_configuration.cross_customer_mesh_allowed``
   - Backticked code identifiers that look like function/method names
   These are the categories where hallucinations cause real damage; backticked
   prose like ``customer_id`` is intentionally excluded to keep noise low.

   Schema note: the PostgreSQL schema is ``trk`` — ``trk.<table>`` references
   remain the correct fully-qualified form.  The ``_SQL_FQNAME_RE`` regex is
   schema-generic and matches any ``trk.*`` name regardless of the underlying
   database engine.

2. `verify_claims(claims, kb_text)` — single Haiku call returning a JSON list
   `[{claim, supported, score, evidence_excerpt}]`. The aggregate score is
   the count-weighted mean. If extraction yields nothing (uninteresting
   answer), we report a neutral 80% confidence by default.

Cost: one extra Haiku call per Discovery answer (~$0.0005). Negligible.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from anthropic import Anthropic

from apps.api.settings import get_settings


# Capture HTTP method + path. Path must look URL-shaped, not prose.
_ENDPOINT_RE = re.compile(
    r"(?:^|\s|\(|`)(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(/[A-Za-z0-9_\-\./:{}=&?]+)",
    re.IGNORECASE,
)
# trk.<table_or_column> with optional backticks. Match longest path.
_SQL_FQNAME_RE = re.compile(r"`?(trk\.[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)?)`?")
# feature_configuration.<flag> (with optional double-quoting/backticks).
_FLAG_RE = re.compile(r"`?(feature_configuration\.[A-Za-z0-9_]+)`?")


@dataclass
class ExtractedClaim:
    text: str
    kind: str   # 'endpoint' | 'sql_table' | 'feature_flag'

    def to_dict(self) -> dict:
        return {"text": self.text, "kind": self.kind}


def extract_claims(markdown: str) -> list[ExtractedClaim]:
    """Pull discrete factual claims from an answer for verification."""
    out: list[ExtractedClaim] = []
    seen: set[tuple[str, str]] = set()

    for m in _ENDPOINT_RE.finditer(markdown):
        text = f"{m.group(1).upper()} {m.group(2)}"
        key = ("endpoint", text)
        if key not in seen:
            seen.add(key)
            out.append(ExtractedClaim(text=text, kind="endpoint"))

    for m in _SQL_FQNAME_RE.finditer(markdown):
        text = m.group(1)
        key = ("sql_table", text)
        if key not in seen:
            seen.add(key)
            out.append(ExtractedClaim(text=text, kind="sql_table"))

    for m in _FLAG_RE.finditer(markdown):
        text = m.group(1)
        key = ("feature_flag", text)
        if key not in seen:
            seen.add(key)
            out.append(ExtractedClaim(text=text, kind="feature_flag"))

    return out


def _band(score: int) -> str:
    if score >= 80:
        return "green"
    if score >= 50:
        return "yellow"
    return "red"


def _verify_with_haiku(claims: list[ExtractedClaim], kb_text: str) -> list[dict]:
    """Single Haiku call. Returns a list shaped like [{text, kind, supported,
    score, evidence_excerpt}]. On any error, falls back to all-supported with
    50% scores so the UI degrades to "no signal" rather than "looks dishonest"."""
    s = get_settings()
    if not s.anthropic_api_key or not claims:
        return [
            {**c.to_dict(), "supported": True, "score": 80, "evidence_excerpt": ""}
            for c in claims
        ]
    payload = [c.to_dict() for c in claims]
    sys_prompt = (
        "You verify whether claims in an AI answer are literally supported by "
        "retrieved corpus chunks. Be strict — minor paraphrases that change "
        "the meaning (e.g. `/devices/v3/history` vs `/devices/v3/configs/get_history`) "
        "are NOT supported.\n\n"
        "Return a JSON array, one object per claim:\n"
        "  {\"text\": str, \"kind\": str, \"supported\": bool, \"score\": int (0-100), "
        "\"evidence_excerpt\": str (≤120 chars from chunk that supports it, or '')}\n\n"
        "Scoring rubric:\n"
        "  90-100: literal substring appears in a chunk\n"
        "  70-89: minor formatting variation but same identity (e.g. case)\n"
        "  40-69: similar shape but different name — likely paraphrase\n"
        "  0-39:  invented or unverified — no chunk supports it\n\n"
        "Examples of verifiable claim kinds in this system:\n"
        "  endpoints: GET /devices/v3/events/latest, POST /external/messages, "
        "GET /devices/v3/configs/history\n"
        "  sql_table: trk.device_event, trk.device, trk.configuration, "
        "trk.raw_device_event, trk.account, trk.facility\n"
        "  feature_flag: feature_configuration.cross_customer_mesh_allowed, "
        "feature_configuration.location_averaging_enabled\n\n"
        "Output ONLY the JSON array. No prose."
    )
    user_msg = (
        f"<claims>\n{json.dumps(payload, indent=2)}\n</claims>\n\n"
        f"<corpus_chunks>\n{kb_text[:20000]}\n</corpus_chunks>"
    )
    try:
        client = Anthropic(api_key=s.anthropic_api_key, timeout=15.0)
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=2000,
            system=[{"type": "text", "text": sys_prompt}],
            messages=[{"role": "user", "content": user_msg}],
        )
        text = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
        # Some models wrap in ```json fences; strip if present.
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE)
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("verifier returned non-list")
        # Sanitize
        cleaned = []
        for item, src in zip(data, payload):
            cleaned.append({
                "text": str(item.get("text") or src["text"])[:200],
                "kind": str(item.get("kind") or src["kind"]),
                "supported": bool(item.get("supported", False)),
                "score": max(0, min(100, int(item.get("score", 0)))),
                "evidence_excerpt": str(item.get("evidence_excerpt") or "")[:240],
            })
        return cleaned
    except Exception:
        # Fail-soft: don't trash an otherwise-good answer.
        return [
            {**c.to_dict(), "supported": True, "score": 50,
             "evidence_excerpt": "(verifier unavailable — could not check)"}
            for c in claims
        ]


def verify_answer(
    answer_md: str, *, kb_text: str,
) -> dict:
    """Compute confidence for a Discovery answer.

    `kb_text` is the same `<knowledge_base>` blob that was passed to the
    answering model. Returns:
      { score: int, band: 'green'|'yellow'|'red',
        claims: [...], extracted_count: int }
    """
    claims = extract_claims(answer_md)
    if not claims:
        return {
            "score": 80,
            "band": "green",
            "claims": [],
            "extracted_count": 0,
            "note": "No verifiable factual claims found in the answer.",
        }
    verified = _verify_with_haiku(claims, kb_text)
    aggregate = int(round(sum(c["score"] for c in verified) / max(1, len(verified))))
    return {
        "score": aggregate,
        "band": _band(aggregate),
        "claims": verified,
        "extracted_count": len(claims),
    }
