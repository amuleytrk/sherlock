import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { listBriefings, getBriefing, runBriefing } from "../lib/api.js";

/**
 * BriefingsPane — proactive-mode dashboard.
 *
 * Lists past briefings (newest first) on the left; the right pane renders
 * the selected briefing's full markdown. A "Run now" button kicks an
 * on-demand briefing using the currently selected env + system.
 *
 * Briefings persist across SHERLOCK_EPHEMERAL_SESSIONS=1 wipes — operators
 * want yesterday's brief even after a server restart.
 */
const SEVERITY_DOT = {
  red: "bg-danger",
  yellow: "bg-warn",
  green: "bg-success",
};
const SEVERITY_LABEL = {
  red: "🔴 red",
  yellow: "🟡 yellow",
  green: "✅ green",
};

export default function BriefingsPane({ env, system }) {
  const [items, setItems] = useState([]);
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);

  async function refresh(autoSelect = true) {
    setLoading(true);
    try {
      const d = await listBriefings();
      setItems(d.briefings || []);
      if (autoSelect && (d.briefings || []).length > 0 && !selected) {
        const first = await getBriefing(d.briefings[0].id);
        setSelected(first);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { refresh(); /* eslint-disable-next-line */ }, []);

  async function runNow() {
    if (running) return;
    setRunning(true);
    setError(null);
    try {
      const rec = await runBriefing({ env: env || null, system: system || null });
      const full = await getBriefing(rec.id);
      setSelected(full);
      await refresh(false);
    } catch (e) {
      setError(e.message);
    } finally {
      setRunning(false);
    }
  }

  async function pick(stub) {
    try {
      const full = await getBriefing(stub.id);
      setSelected(full);
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <div className="h-full flex flex-col md:flex-row overflow-hidden">
      {/* Left list */}
      <aside className="w-full md:w-72 md:border-r border-outline-soft bg-surface-2/30 flex flex-col">
        <div className="p-3 border-b border-outline-soft">
          <button
            onClick={runNow}
            disabled={running}
            className="w-full px-3 py-2 rounded bg-primary text-primary-fg font-medium hover:opacity-90 disabled:opacity-50 transition"
          >
            {running ? "Running probes…" : "▶ Run briefing now"}
          </button>
          <div className="text-[10px] font-tech text-ink-muted mt-1.5">
            env: <span className="text-primary">{env || "?"}</span> · db: <span className="text-primary">{system || "?"}</span>
          </div>
        </div>
        <ul className="flex-1 overflow-y-auto">
          {loading && <li className="p-3 text-xs text-ink-muted">loading…</li>}
          {!loading && items.length === 0 && (
            <li className="p-3 text-xs text-ink-muted">No briefings yet. Click "Run briefing now" above.</li>
          )}
          {items.map((b) => {
            const isActive = selected?.id === b.id;
            return (
              <li key={b.id}>
                <button
                  onClick={() => pick(b)}
                  className={`
                    w-full text-left px-3 py-2.5 border-b border-outline-soft/40
                    transition flex gap-2 items-start
                    ${isActive ? "bg-surface-3 border-l-2 border-l-primary" : "hover:bg-surface-2"}
                  `}
                >
                  <span className={`mt-1.5 inline-block w-2 h-2 rounded-full flex-shrink-0 ${SEVERITY_DOT[b.severity] || SEVERITY_DOT.green}`} />
                  <span className="flex-1 min-w-0">
                    <span className="block text-sm text-ink truncate">{b.title}</span>
                    <span className="block text-[10px] font-tech text-ink-muted">
                      {b.created_at?.slice(0, 16).replace("T", " ")} · {b.env} · {b.triggered_by}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </aside>

      {/* Right detail */}
      <main className="flex-1 overflow-y-auto p-6">
        {error && (
          <div className="mb-4 px-3 py-2 rounded bg-danger/10 border border-danger/40 text-danger text-sm">
            {error}
          </div>
        )}
        {!selected && !loading && (
          <Welcome />
        )}
        {selected && (
          <article>
            <header className="mb-4 pb-4 border-b border-outline-soft">
              <div className="flex items-center gap-2 mb-1">
                <span className={`inline-block w-2.5 h-2.5 rounded-full ${SEVERITY_DOT[selected.severity]}`} />
                <span className="label-caps">{SEVERITY_LABEL[selected.severity]}</span>
                <span className="label-caps text-ink-muted">·</span>
                <span className="label-caps text-ink-muted">{selected.env} / {selected.system}</span>
                <span className="label-caps text-ink-muted">·</span>
                <span className="label-caps text-ink-muted">{selected.triggered_by}</span>
                <span className="ml-auto text-[10px] font-tech text-ink-muted">
                  {selected.duration_ms} ms · {selected.created_at?.slice(0, 16).replace("T", " ")}
                </span>
              </div>
            </header>
            <div className="prose-invert max-w-none">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{selected.content_md || ""}</ReactMarkdown>
            </div>
          </article>
        )}
      </main>
    </div>
  );
}

function Welcome() {
  return (
    <div className="max-w-xl mx-auto py-8 space-y-3">
      <h2 className="mb-2">Sherlock Briefings</h2>
      <p className="text-ink-dim text-sm">
        Sherlock runs scheduled health probes against the active env and produces
        a markdown brief whenever something looks off — pod restart spikes,
        milestone insert failures, Redis socket flaps, ingress 5xx clusters.
      </p>
      <p className="text-ink-dim text-sm">
        Click <span className="font-tech text-primary">▶ Run briefing now</span> to
        trigger one on demand. The Anthropic Haiku model adds a 2-3 sentence
        likely-cause assessment for each anomaly so you arrive with a head start.
      </p>
    </div>
  );
}
