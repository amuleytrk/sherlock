import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { getRca, artifactUrl } from "../lib/api.js";
import MermaidBlock from "./MermaidBlock.jsx";
import AuditTrail from "./AuditTrail.jsx";

export default function RcaReport({ rcaId }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    let alive = true;
    getRca(rcaId)
      .then((d) => { if (alive) setData(d); })
      .catch((e) => { if (alive) setErr(String(e)); });
    return () => { alive = false; };
  }, [rcaId]);

  if (err) return <div className="text-sm text-danger">{err}</div>;
  if (!data) return <div className="text-sm text-ink-muted">loading rca…</div>;

  const md = data.final_rca_markdown || "*(No final RCA written yet — agent may have hit the tool-call cap.)*";
  const mermaidFiles = (data.analysis_files || []).filter((f) => f.name.endsWith(".mmd"));
  const imgFiles = (data.analysis_files || []).filter((f) => f.name.match(/\.(png|jpe?g|svg)$/i));

  return (
    <article className="rounded-lg border border-outline-soft bg-surface p-5 space-y-4">
      <header className="flex items-baseline gap-3 flex-wrap">
        <h2>Root Cause Analysis</h2>
        <span className="label-caps">{data.rca_id}</span>
        <span className="ml-auto text-xs font-tech text-ink-muted">{data.meta?.created_at?.slice(0, 16)}</span>
      </header>

      <div className="prose-invert">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{md}</ReactMarkdown>
      </div>

      {mermaidFiles.length > 0 && (
        <section>
          <h3 className="mb-2">Diagrams</h3>
          {mermaidFiles.map((f) => <MermaidArtifact key={f.path} path={f.path} />)}
        </section>
      )}

      {imgFiles.length > 0 && (
        <section>
          <h3 className="mb-2">Charts</h3>
          <div className="grid gap-3">
            {imgFiles.map((f) => (
              <img key={f.path} src={artifactUrl(f.path)} alt={f.name}
                   className="rounded-lg border border-outline-soft" />
            ))}
          </div>
        </section>
      )}

      {data.evidence_files?.length > 0 && (
        <details className="border border-outline-soft rounded-lg p-3">
          <summary className="label-caps cursor-pointer">
            Evidence files ({data.evidence_files.length})
          </summary>
          <ul className="mt-2 space-y-1">
            {data.evidence_files.map((f) => (
              <li key={f.path} className="text-xs font-tech text-ink-dim">
                <a href={artifactUrl(f.path)} target="_blank" rel="noreferrer"
                   className="hover:text-primary">{f.name}</a>
              </li>
            ))}
          </ul>
        </details>
      )}

      <AuditTrail rcaId={data.rca_id} />
    </article>
  );
}

function MermaidArtifact({ path }) {
  const [src, setSrc] = useState(null);
  useEffect(() => {
    let alive = true;
    fetch(artifactUrl(path))
      .then((r) => r.text())
      .then((t) => { if (alive) setSrc(t); });
    return () => { alive = false; };
  }, [path]);
  if (!src) return null;
  return <MermaidBlock source={src} />;
}
