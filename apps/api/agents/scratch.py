"""Per-investigation scratch-dir manager.

Sherlock follows the Microsoft SRE-Agent "filesystem-as-context" pattern:
every tool call's output is written to a numbered file under
`<root>/<rca_id>/evidence/`. The agent then uses `read_file`/`grep` to
navigate its own evidence rather than re-calling tools to re-fetch data.

Scratch dir layout:

    <root>/<rca_id>/
        meta.json
        plan.md                   (optional — early plan from the agent)
        evidence/
            001-<slug>.<ext>
            002-<slug>.<ext>
            ...
        analysis/
            timeline.png          (matplotlib output via Code Execution)
            service-hops.mmd      (Mermaid diagram source)
            notes.md
        final-rca.md
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


_SLUG = re.compile(r"[^a-z0-9_-]+")


def _slugify(s: str) -> str:
    return _SLUG.sub("-", s.lower()).strip("-")[:60] or "item"


@dataclass
class Investigation:
    root: Path
    rca_id: str

    @property
    def dir(self) -> Path:
        return self.root / self.rca_id

    @property
    def evidence_dir(self) -> Path:
        return self.dir / "evidence"

    @property
    def analysis_dir(self) -> Path:
        return self.dir / "analysis"

    @classmethod
    def create(
        cls, root: Path, rca_id: str, user_query: str, entities: dict
    ) -> "Investigation":
        inv = cls(root=root, rca_id=rca_id)
        inv.dir.mkdir(parents=True, exist_ok=True)
        inv.evidence_dir.mkdir(exist_ok=True)
        inv.analysis_dir.mkdir(exist_ok=True)
        meta = {
            "rca_id": rca_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "user_query": user_query,
            "entities": entities,
        }
        (inv.dir / "meta.json").write_text(json.dumps(meta, indent=2))
        return inv

    @classmethod
    def load(cls, root: Path, rca_id: str) -> "Investigation":
        return cls(root=root, rca_id=rca_id)

    def write_evidence(self, name: str, ext: str, content: str) -> Path:
        existing = sorted(self.evidence_dir.glob("*.*"))
        n = len(existing) + 1
        path = self.evidence_dir / f"{n:03d}-{_slugify(name)}.{ext}"
        path.write_text(content)
        return path

    def list_evidence(self) -> list[Path]:
        return sorted(self.evidence_dir.glob("*.*"))

    def write_final_rca(self, markdown: str) -> Path:
        path = self.dir / "final-rca.md"
        path.write_text(markdown)
        return path

    def write_analysis(self, name: str, content: str | bytes) -> Path:
        path = self.analysis_dir / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)
        return path

    def list_analysis(self) -> list[Path]:
        return sorted(self.analysis_dir.glob("*"))

    def read_meta(self) -> dict:
        meta_path = self.dir / "meta.json"
        if not meta_path.exists():
            return {}
        return json.loads(meta_path.read_text())
