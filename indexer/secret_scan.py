"""Per-chunk secret detection. Chunks containing literal secrets are dropped
before embedding so the LLM cannot accidentally retrieve them.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from detect_secrets import SecretsCollection
from detect_secrets.settings import default_settings


def contains_secret(text: str) -> bool:
    """Return True if `text` contains any pattern detect-secrets considers a secret."""
    with default_settings():
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write(text)
            tmp_path = f.name
        try:
            sc = SecretsCollection()
            sc.scan_file(tmp_path)
            return bool(list(sc))
        finally:
            Path(tmp_path).unlink(missing_ok=True)
