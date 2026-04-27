import { useEffect, useState } from "react";
import { listSessions } from "../lib/api.js";

export default function HistorySidebar({ activeSession, onSelect, onNew }) {
  const [history, setHistory] = useState([]);

  useEffect(() => {
    let alive = true;
    listSessions()
      .then((d) => { if (alive) setHistory(d.sessions || []); })
      .catch(() => { if (alive) setHistory([]); });
    return () => { alive = false; };
  }, []);

  return (
    <div className="h-full flex flex-col p-4">
      <div className="flex items-baseline gap-2 mb-1">
        <h1 className="text-[18px] font-semibold tracking-tight">SHERLOCK</h1>
      </div>
      <span className="label-caps mb-4">RCA + API Discovery</span>

      <button
        onClick={onNew}
        className="mb-4 px-3 py-2 rounded bg-primary text-primary-fg font-medium hover:opacity-90 transition"
      >
        + New investigation
      </button>

      <span className="label-caps mb-2">History</span>
      <ul className="flex-1 overflow-y-auto space-y-1 -mx-1 px-1">
        {history.length === 0 && (
          <li className="text-sm text-ink-muted px-2">No past sessions yet.</li>
        )}
        {history.map((s) => (
          <li key={s.id}>
            <button
              onClick={() => onSelect(s)}
              className={`
                w-full text-left px-3 py-2 rounded text-sm transition
                ${activeSession?.id === s.id
                  ? "bg-surface-3 text-primary border-l-2 border-primary"
                  : "hover:bg-surface-2 text-ink-dim"}
              `}
            >
              <div className="truncate">{s.title || "Untitled"}</div>
              <div className="text-xs text-ink-muted font-tech">{s.created_at?.slice(0, 16)}</div>
            </button>
          </li>
        ))}
      </ul>

      <div className="mt-4 pt-4 border-t border-outline-soft">
        <a
          href="https://github.com/amuleytrk/sherlock"
          target="_blank" rel="noreferrer"
          className="text-xs text-ink-muted font-tech hover:text-primary"
        >
          github.com/amuleytrk/sherlock
        </a>
      </div>
    </div>
  );
}
