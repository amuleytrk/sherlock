import { useEffect, useState } from "react";
import { getAuditTrail } from "../lib/api.js";

export default function AuditTrail({ rcaId }) {
  const [entries, setEntries] = useState([]);

  useEffect(() => {
    let alive = true;
    getAuditTrail(rcaId)
      .then((d) => { if (alive) setEntries(d.entries || []); })
      .catch(() => { /* silent */ });
    return () => { alive = false; };
  }, [rcaId]);

  if (!entries.length) return null;
  return (
    <details className="rounded-lg border border-outline-soft bg-surface-2 p-3">
      <summary className="label-caps cursor-pointer">
        Audit log ({entries.length} tool calls)
      </summary>
      <table className="text-xs w-full mt-2">
        <thead>
          <tr className="border-b border-outline-soft">
            <th className="label-caps text-left px-2 py-1 whitespace-nowrap">when</th>
            <th className="label-caps text-left px-2 py-1 whitespace-nowrap">tool</th>
            <th className="label-caps text-left px-2 py-1">args</th>
            <th className="label-caps text-right px-2 py-1 whitespace-nowrap">ms</th>
            <th className="label-caps text-left px-2 py-1 whitespace-nowrap">outcome</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e, i) => (
            <tr key={i} className={i % 2 ? "bg-surface" : ""}>
              <td className="font-tech px-2 py-1 text-ink-muted whitespace-nowrap">{e.created_at?.slice(11, 19)}</td>
              <td className="font-tech px-2 py-1 whitespace-nowrap">{e.tool_name}</td>
              <td className="font-tech px-2 py-1 text-ink-dim truncate max-w-xs">{e.args_json}</td>
              <td className="font-tech px-2 py-1 text-right whitespace-nowrap">{e.duration_ms}</td>
              <td className={`font-tech px-2 py-1 whitespace-nowrap ${e.outcome === "ok" ? "text-success" : "text-danger"}`}>{e.outcome}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}
