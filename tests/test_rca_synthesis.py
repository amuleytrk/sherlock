"""Regression tests for the Opus synthesis / retry message construction.

Bug (observed in PPE): when the first synthesis response contained a tool_use
block (an empty write_final_rca, or a stray investigative call), the retry path
appended a PLAIN-TEXT user message — orphaning the tool_use. Anthropic then
400s the retry call: "tool_use ids were found without tool_result blocks
immediately after". Every tool_use MUST be answered by a tool_result in the
immediately-following message.
"""
from apps.api.agents.rca import _retry_user_message, _content_to_history_block


class _ToolUse:
    type = "tool_use"
    def __init__(self, id, name, inp):
        self.id, self.name, self.input = id, name, inp


class _Text:
    type = "text"
    def __init__(self, text):
        self.text = text


def _assert_tool_uses_answered(history):
    """Mirror the Anthropic invariant: each assistant tool_use id has a
    tool_result in the very next (user) message."""
    for i, msg in enumerate(history):
        if msg["role"] != "assistant" or not isinstance(msg["content"], list):
            continue
        tu_ids = [b["id"] for b in msg["content"]
                  if isinstance(b, dict) and b.get("type") == "tool_use"]
        if not tu_ids:
            continue
        assert i + 1 < len(history), f"assistant tool_use at msg {i} is the last message"
        nxt = history[i + 1]
        assert nxt["role"] == "user" and isinstance(nxt["content"], list), \
            f"tool_use at msg {i} not followed by a tool_result user message"
        tr_ids = {b["tool_use_id"] for b in nxt["content"]
                  if isinstance(b, dict) and b.get("type") == "tool_result"}
        missing = set(tu_ids) - tr_ids
        assert not missing, f"tool_use ids {missing} at msg {i} have no tool_result"


def test_retry_answers_orphaned_tool_use():
    # First synthesis emitted a (bad) write_final_rca tool_use with empty markdown.
    resp = [_Text("Let me write it."), _ToolUse("toolu_0143", "write_final_rca", {})]
    history = [
        {"role": "user", "content": "investigate"},
        {"role": "assistant", "content": _content_to_history_block(resp)},
        _retry_user_message(resp, "Call write_final_rca with the full markdown."),
    ]
    _assert_tool_uses_answered(history)  # was the 400; must not raise now


def test_retry_answers_multiple_parallel_tool_uses():
    resp = [_ToolUse("a", "trk_postgres_query", {}), _ToolUse("b", "sherlock_search", {})]
    history = [
        {"role": "assistant", "content": _content_to_history_block(resp)},
        _retry_user_message(resp, "synthesize now"),
    ]
    _assert_tool_uses_answered(history)


def test_retry_plain_text_when_no_tool_use():
    # Text-only synthesis response → no tool_use to orphan → plain text is fine.
    resp = [_Text("I have sufficient evidence.")]
    assert _retry_user_message(resp, "directive text") == {"role": "user", "content": "directive text"}
