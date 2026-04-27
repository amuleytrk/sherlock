"""Anthropic Code Execution wrapper.

Runs Python in Anthropic's hosted sandbox over the per-investigation scratch
dir. The agent's tool args specify code; this module:

1. Uploads all evidence files into a Code Execution container via the Files API.
2. Runs the code in the container.
3. Pulls any newly created files (e.g. matplotlib PNGs, .mmd Mermaid sources)
   back into investigation.analysis_dir.

The container has NO database credentials and NO network access — it can
ONLY read the uploaded evidence files. The agent gets pandas + matplotlib
preinstalled in the sandbox.

If the SDK's beta names shift between versions, only `_call_code_exec` and
the response parsing logic should need updates.
"""
from __future__ import annotations

import base64
from pathlib import Path

from anthropic import Anthropic

from apps.api.agents.scratch import Investigation
from apps.api.settings import get_settings


def _client() -> Anthropic:
    s = get_settings()
    return Anthropic(api_key=s.anthropic_api_key)


def run_code_exec_against_scratch(investigation: Investigation, code: str) -> dict:
    """Upload evidence files → run `code` → return stdout/stderr + produced files.

    Returns: `{"stdout": str, "stderr": str, "produced_files": [Path, ...]}`

    Produced files are written into `investigation.analysis_dir/`.

    If the Anthropic API key is empty, returns a stubbed result with a clear
    error so callers don't silently no-op.
    """
    s = get_settings()
    if not s.anthropic_api_key:
        return {
            "stdout": "",
            "stderr": "ANTHROPIC_API_KEY not set — code_exec unavailable",
            "produced_files": [],
        }

    client = _client()
    file_ids: list[str] = []

    # Upload every evidence file to the Anthropic Files API
    for path in investigation.list_evidence():
        try:
            with open(path, "rb") as f:
                uploaded = client.beta.files.upload(
                    file=(path.name, f, "application/octet-stream"),
                )
                file_ids.append(uploaded.id)
        except Exception as e:
            # Don't abort the whole run if a single file fails to upload
            print(f"warn: failed to upload {path}: {e}")

    user_msg = (
        "Run this Python in the code execution sandbox over the uploaded "
        "investigation files. Save matplotlib charts as PNGs in /tmp/, "
        "and any Mermaid diagrams as .mmd files in /tmp/.\n\n"
        f"```python\n{code}\n```"
    )

    try:
        resp = client.beta.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4000,
            tools=[{"type": "code_execution_20250825", "name": "code_execution"}],
            betas=["files-api-2025-04-14", "code-execution-2025-08-25"],
            container={"file_ids": file_ids} if file_ids else None,
            messages=[{"role": "user", "content": user_msg}],
        )
    except Exception as e:
        return {
            "stdout": "",
            "stderr": f"code_exec API error: {type(e).__name__}: {e}",
            "produced_files": [],
        }

    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    produced: list[Path] = []

    for block in resp.content:
        if getattr(block, "type", None) != "tool_result":
            continue
        sub_blocks = getattr(block, "content", None) or []
        for sub in sub_blocks:
            sub_type = getattr(sub, "type", None)
            if sub_type == "code_execution_output":
                stdout_parts.append(getattr(sub, "stdout", "") or "")
                stderr_parts.append(getattr(sub, "stderr", "") or "")
                inner = getattr(sub, "content", None) or []
                for cf in inner:
                    cf_type = getattr(cf, "type", None)
                    if cf_type == "code_execution_output_image":
                        try:
                            data = base64.b64decode(cf.source.data)
                            target = investigation.analysis_dir / cf.filename
                            target.write_bytes(data)
                            produced.append(target)
                        except Exception as e:
                            stderr_parts.append(f"failed to extract image {cf.filename}: {e}")
                    elif cf_type == "code_execution_output_file":
                        # Text outputs (e.g. .mmd) come through as text files
                        try:
                            text = getattr(cf, "text", None)
                            if text is None and getattr(cf, "source", None) is not None:
                                text = base64.b64decode(cf.source.data).decode("utf-8", errors="replace")
                            if text is not None:
                                target = investigation.analysis_dir / cf.filename
                                target.write_text(text)
                                produced.append(target)
                        except Exception as e:
                            stderr_parts.append(f"failed to extract file {cf.filename}: {e}")

    return {
        "stdout": "\n".join(stdout_parts),
        "stderr": "\n".join(stderr_parts),
        "produced_files": produced,
    }
