import { useEffect } from "react";

/**
 * SystemSwitcher — segmented toggle between MSSQL and Postgres.
 *
 * Trackonomy is mid-migration MSSQL → PostgreSQL, and the indexed corpus
 * has docs from both eras. This toggle scopes RAG retrieval to one system
 * so e.g. an MSSQL-mode discovery query never surfaces PG-flavored tables
 * like `trk.raw_device_event`. Selection persists to localStorage; the
 * parent passes the value back down to ChatStream so each chat request
 * includes it.
 *
 * Two-option toggle (rather than a dropdown) since the choice is binary
 * today; trivially extends to a dropdown later if a third DB era ever lands.
 */
const STORAGE_KEY = "sherlock.system";
const OPTIONS = [
  { value: "mssql", label: "MSSQL" },
  { value: "postgres", label: "Postgres" },
];

export default function SystemSwitcher({ value, onChange }) {
  // On first mount, hydrate from localStorage if the parent hasn't picked a
  // value yet. Default to mssql (current production reality at Trackonomy).
  useEffect(() => {
    if (!value) {
      const stored = localStorage.getItem(STORAGE_KEY);
      const initial = OPTIONS.some((o) => o.value === stored) ? stored : "mssql";
      onChange?.(initial);
    }
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  function pick(v) {
    onChange?.(v);
    localStorage.setItem(STORAGE_KEY, v);
  }

  return (
    <div className="inline-flex items-center gap-1.5 px-1 py-0.5 rounded-lg bg-surface-2 border border-outline-soft">
      <span className="label-caps pl-1.5">db</span>
      {OPTIONS.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            onClick={() => pick(o.value)}
            className={`
              px-2 py-0.5 rounded text-xs font-tech transition
              ${active
                ? "bg-primary text-primary-fg"
                : "text-ink-dim hover:text-ink hover:bg-surface-3"}
            `}
            aria-pressed={active}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
