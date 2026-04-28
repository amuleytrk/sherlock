import { useEffect, useState } from "react";
import { getHealth } from "../lib/api.js";

/**
 * EnvSwitcher — currently a read-only env badge.
 *
 * Sherlock supports only one environment at a time (whatever SHERLOCK_RELEASE
 * in `.env` declares — typically "ppe"). The dropdown was misleading because
 * flipping it had no effect; we now display the live value from /health.
 *
 * If we ever support multiple environments simultaneously, restore a real
 * dropdown that actually round-trips the choice to the backend.
 */
export default function EnvSwitcher() {
  const [env, setEnv] = useState("…");
  const [demoMode, setDemoMode] = useState(false);

  useEffect(() => {
    let alive = true;
    getHealth()
      .then((h) => {
        if (!alive) return;
        setEnv((h.release || "?").toUpperCase());
        setDemoMode(Boolean(h.demo_mode));
      })
      .catch(() => alive && setEnv("?"));
    return () => { alive = false; };
  }, []);

  return (
    <div className="inline-flex items-center gap-2">
      <div className="inline-flex items-center gap-1 px-2 py-1 rounded-lg bg-surface-2 border border-outline-soft">
        <span className="label-caps">env</span>
        <span className="text-sm text-primary font-tech">{env}</span>
      </div>
      {demoMode && (
        <div className="inline-flex items-center gap-1 px-2 py-1 rounded-lg bg-warn/10 border border-warn/40">
          <span className="label-caps text-warn">demo mode</span>
        </div>
      )}
    </div>
  );
}
