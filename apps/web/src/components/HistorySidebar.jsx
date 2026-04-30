import { useEffect, useState } from "react";
import { listSessions, deleteSession, deleteAllSessions } from "../lib/api.js";

export default function HistorySidebar({
  activeSession,
  onSelect,
  onNew,
  onDeleted,
  refreshKey = 0,
}) {
  const [history, setHistory] = useState([]);
  const [busyId, setBusyId] = useState(null);
  const [confirmingClearAll, setConfirmingClearAll] = useState(false);

  useEffect(() => {
    let alive = true;
    listSessions()
      .then((d) => { if (alive) setHistory(d.sessions || []); })
      .catch(() => { if (alive) setHistory([]); });
    return () => { alive = false; };
  }, [refreshKey]);

  async function handleDeleteOne(e, s) {
    e.stopPropagation();
    if (busyId) return;
    if (!confirm(`Delete "${s.title || "Untitled"}"? This also removes its RCA scratch dirs.`)) return;
    setBusyId(s.id);
    try {
      await deleteSession(s.id);
      setHistory((h) => h.filter((x) => x.id !== s.id));
      onDeleted?.(s.id);
    } catch (err) {
      alert(`Delete failed: ${err.message}`);
    } finally {
      setBusyId(null);
    }
  }

  async function handleClearAll() {
    if (busyId) return;
    setBusyId("__all__");
    try {
      await deleteAllSessions();
      setHistory([]);
      setConfirmingClearAll(false);
      onDeleted?.("__all__");
    } catch (err) {
      alert(`Clear-all failed: ${err.message}`);
    } finally {
      setBusyId(null);
    }
  }

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

      <div className="flex items-center justify-between mb-2">
        <span className="label-caps">History</span>
        {history.length > 0 && !confirmingClearAll && (
          <button
            onClick={() => setConfirmingClearAll(true)}
            className="text-[10px] font-tech text-ink-muted hover:text-danger transition"
            disabled={busyId === "__all__"}
          >
            Clear all
          </button>
        )}
        {confirmingClearAll && (
          <div className="flex items-center gap-1.5">
            <button
              onClick={handleClearAll}
              className="text-[10px] font-tech text-danger hover:opacity-80"
              disabled={busyId === "__all__"}
            >
              {busyId === "__all__" ? "wiping..." : "confirm"}
            </button>
            <span className="text-[10px] text-ink-muted">·</span>
            <button
              onClick={() => setConfirmingClearAll(false)}
              className="text-[10px] font-tech text-ink-muted hover:text-ink"
              disabled={busyId === "__all__"}
            >
              cancel
            </button>
          </div>
        )}
      </div>

      <ul className="flex-1 overflow-y-auto space-y-1 -mx-1 px-1">
        {history.length === 0 && (
          <li className="text-sm text-ink-muted px-2">No past sessions yet.</li>
        )}
        {history.map((s) => (
          <li key={s.id} className="group relative">
            <button
              onClick={() => onSelect(s)}
              className={`
                w-full text-left pl-3 pr-9 py-2 rounded text-sm transition
                ${activeSession?.id === s.id
                  ? "bg-surface-3 text-primary border-l-2 border-primary"
                  : "hover:bg-surface-2 text-ink-dim"}
              `}
            >
              <div className="truncate">{s.title || "Untitled"}</div>
              <div className="text-xs text-ink-muted font-tech">{s.created_at?.slice(0, 16)}</div>
            </button>
            <button
              onClick={(e) => handleDeleteOne(e, s)}
              disabled={busyId === s.id}
              aria-label="Delete session"
              className={`
                absolute right-1.5 top-1/2 -translate-y-1/2
                w-7 h-7 rounded flex items-center justify-center
                opacity-0 group-hover:opacity-100 focus:opacity-100
                hover:bg-surface-3 hover:text-danger transition
                ${busyId === s.id ? "opacity-100 text-ink-muted" : "text-ink-muted"}
              `}
            >
              {busyId === s.id ? (
                <span className="text-[10px] font-tech">...</span>
              ) : (
                /* trash icon */
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                  <path d="M3 4h8M5.5 4V3a1 1 0 011-1h1a1 1 0 011 1v1M4 4l.5 7.5a1 1 0 001 1h3a1 1 0 001-1L10 4M6 6.5v4M8 6.5v4"
                        stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              )}
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
