import MermaidBlock from "./MermaidBlock.jsx";
import { artifactUrl } from "../lib/api.js";

export default function EvidenceCard({ kind, content }) {
  if (kind === "log" || kind === "logs") return <LogCard lines={content?.lines || []} />;
  if (kind === "table" || kind === "json_table") return <TableCard rows={content?.rows || content || []} />;
  if (kind === "mermaid") return <MermaidCard source={content?.source || content} />;
  if (kind === "image" || kind === "png") return <ImageCard path={content?.path} dataUrl={content?.dataUrl} caption={content?.caption} />;
  if (kind === "citation_list") return <CitationList items={content?.items || []} />;
  if (kind === "raw") return <RawCard text={typeof content === "string" ? content : JSON.stringify(content, null, 2)} />;
  return <RawCard text={JSON.stringify(content, null, 2)} />;
}

function LogCard({ lines }) {
  return (
    <div className="rounded-lg border border-outline-soft bg-surface-2 overflow-hidden">
      <div className="label-caps px-3 py-2 border-b border-outline-soft">log snippet</div>
      <pre className="p-3 text-xs font-tech text-ink-dim overflow-x-auto whitespace-pre">
        {lines.map((l, i) => (
          <div key={i} className={
            /\[ERROR\]|level=error/i.test(l) ? "text-danger" :
            /\[WARN\]|level=warn/i.test(l) ? "text-warn" : ""
          }>{l}</div>
        ))}
      </pre>
    </div>
  );
}

function TableCard({ rows }) {
  if (!rows.length) return <div className="text-xs text-ink-muted px-3 py-2">(no rows)</div>;
  const cols = Object.keys(rows[0]);
  return (
    <div className="rounded-lg border border-outline-soft bg-surface-2 overflow-x-auto">
      <table className="text-xs w-full">
        <thead>
          <tr className="border-b border-outline-soft">
            {cols.map((c) => <th key={c} className="label-caps text-left px-3 py-2 whitespace-nowrap">{c}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className={i % 2 ? "bg-surface" : ""}>
              {cols.map((c) => <td key={c} className="px-3 py-1.5 font-tech text-ink-dim whitespace-nowrap">{String(r[c] ?? "")}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MermaidCard({ source }) {
  return (
    <div className="rounded-lg border border-outline-soft bg-surface-2 p-3">
      <div className="label-caps mb-2">diagram</div>
      <MermaidBlock source={source} />
    </div>
  );
}

function ImageCard({ path, dataUrl, caption }) {
  const src = dataUrl || (path ? artifactUrl(path) : null);
  if (!src) return null;
  return (
    <figure className="rounded-lg border border-outline-soft bg-surface-2 overflow-hidden">
      <img src={src} alt={caption || "evidence"} className="w-full h-auto" />
      {caption && <figcaption className="px-3 py-2 text-xs text-ink-muted">{caption}</figcaption>}
    </figure>
  );
}

function CitationList({ items }) {
  return (
    <div className="rounded-lg border border-outline-soft bg-surface-2 p-3">
      <div className="label-caps mb-2">sources</div>
      <ul className="space-y-1">
        {items.map((c, i) => (
          <li key={i} className="text-xs font-tech text-ink-dim flex flex-wrap gap-x-2">
            <span className="text-primary">{c.service || c.category}</span>
            <span className="text-ink-muted">·</span>
            <span className="break-all">{c.file_path}:{c.line_start}-{c.line_end}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function RawCard({ text }) {
  return (
    <div className="rounded-lg border border-outline-soft bg-surface-2 overflow-hidden">
      <div className="label-caps px-3 py-2 border-b border-outline-soft">raw</div>
      <pre className="p-3 text-xs font-tech text-ink-dim overflow-x-auto whitespace-pre-wrap">{text}</pre>
    </div>
  );
}
