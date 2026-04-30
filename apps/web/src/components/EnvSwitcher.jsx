import { useEffect, useRef, useState } from "react";
import { getHealth, listEnvs } from "../lib/api.js";

/**
 * Top-right env dropdown.
 *
 * Lists every env declared in SHERLOCK_ENVS, with per-tool availability dots
 * (mssql / cosmos / redis / kubectl) so the user can see at a glance which
 * envs are fully wired and which are still missing creds. Selection persists
 * to localStorage; the parent passes the value back down to ChatStream so
 * each chat request includes it.
 *
 * Adding a new env (prod, etc.) requires zero changes here — the backend's
 * /envs response drives the list.
 */
const TOOL_ORDER = ["kubectl", "mssql", "cosmos", "redis", "datadog"];
const STORAGE_KEY = "sherlock.env";

export default function EnvSwitcher({ value, onChange }) {
  const [envs, setEnvs] = useState([]);     // [{name, availability}]
  const [defaultEnv, setDefaultEnv] = useState("ppe");
  const [demoMode, setDemoMode] = useState(false);
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);

  useEffect(() => {
    let alive = true;
    Promise.all([listEnvs(), getHealth()])
      .then(([e, h]) => {
        if (!alive) return;
        setEnvs(e.envs || []);
        setDefaultEnv(e.default || "ppe");
        setDemoMode(Boolean(h.demo_mode));
        // Initial value: prefer prop, else localStorage, else backend default.
        // Validate that whatever we pick is actually configured.
        if (!value) {
          const stored = localStorage.getItem(STORAGE_KEY);
          const configured = (e.envs || []).map((x) => x.name);
          const initial = configured.includes(stored) ? stored : (e.default || configured[0] || "");
          if (initial) onChange?.(initial);
        }
      })
      .catch(() => alive && setEnvs([]));
    return () => { alive = false; };
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  // Click-outside to close menu
  useEffect(() => {
    if (!open) return;
    function onDocClick(e) {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  function pick(name) {
    onChange?.(name);
    localStorage.setItem(STORAGE_KEY, name);
    setOpen(false);
  }

  const active = envs.find((e) => e.name === value);
  const label = (value || defaultEnv || "?").toUpperCase();

  return (
    <div className="inline-flex items-center gap-2" ref={rootRef}>
      <div className="relative">
        <button
          onClick={() => setOpen((v) => !v)}
          className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-surface-2 border border-outline-soft hover:border-primary/50 transition"
          aria-haspopup="listbox"
          aria-expanded={open}
        >
          <span className="label-caps">env</span>
          <span className="text-sm text-primary font-tech">{label}</span>
          <svg width="10" height="10" viewBox="0 0 10 10" className="text-ink-muted">
            <path d="M2 4l3 3 3-3" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round" />
          </svg>
        </button>

        {open && envs.length > 0 && (
          <div
            className="absolute right-0 mt-1 w-64 rounded-lg bg-surface border border-outline-soft shadow-lg z-30 overflow-hidden"
            role="listbox"
          >
            {envs.map((e) => {
              const allTools = TOOL_ORDER.filter((t) => e.availability[t]).length;
              const totalTools = TOOL_ORDER.length;
              const isActive = e.name === value;
              return (
                <button
                  key={e.name}
                  onClick={() => pick(e.name)}
                  className={`
                    w-full px-3 py-2 text-left flex items-center gap-2
                    hover:bg-surface-2 transition
                    ${isActive ? "bg-surface-3" : ""}
                  `}
                  role="option"
                  aria-selected={isActive}
                >
                  <span className={`text-sm font-tech ${isActive ? "text-primary" : "text-ink"}`}>
                    {e.name.toUpperCase()}
                  </span>
                  <span className="ml-auto inline-flex items-center gap-1">
                    {TOOL_ORDER.map((t) => (
                      <span
                        key={t}
                        title={`${t}: ${e.availability[t] ? "configured" : "not configured"}`}
                        className={`w-1.5 h-1.5 rounded-full ${e.availability[t] ? "bg-success" : "bg-outline-soft"}`}
                      />
                    ))}
                    <span className="ml-1 text-[10px] font-tech text-ink-muted">
                      {allTools}/{totalTools}
                    </span>
                  </span>
                </button>
              );
            })}
            <div className="px-3 py-2 border-t border-outline-soft text-[10px] font-tech text-ink-muted">
              dots: kubectl · mssql · cosmos · redis · datadog
            </div>
          </div>
        )}
      </div>
      {demoMode && (
        <div className="inline-flex items-center gap-1 px-2 py-1 rounded-lg bg-warn/10 border border-warn/40">
          <span className="label-caps text-warn">demo mode</span>
        </div>
      )}
    </div>
  );
}
