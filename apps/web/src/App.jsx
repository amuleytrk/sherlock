import { useState } from "react";
import HistorySidebar from "./components/HistorySidebar.jsx";
import ChatStream from "./components/ChatStream.jsx";
import EnvSwitcher from "./components/EnvSwitcher.jsx";

export default function App() {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [activeSession, setActiveSession] = useState(null);
  const [env, setEnv] = useState("ppe");

  return (
    <div className="h-screen w-screen flex flex-col">
      {/* Top bar */}
      <header className="glass border-b border-outline-soft flex items-center gap-3 px-4 py-3 z-20">
        <button
          onClick={() => setDrawerOpen((v) => !v)}
          className="md:hidden p-2 -ml-2 rounded hover:bg-surface-3 active:bg-surface-3"
          aria-label="Toggle history"
        >
          <span className="block w-5 h-0.5 bg-ink mb-1.5" />
          <span className="block w-5 h-0.5 bg-ink mb-1.5" />
          <span className="block w-5 h-0.5 bg-ink" />
        </button>
        <h1 className="text-[18px] font-semibold tracking-tight">Sherlock</h1>
        <span className="hidden sm:inline label-caps ml-2">RCA + API DISCOVERY</span>
        <div className="ml-auto"><EnvSwitcher env={env} onChange={setEnv} /></div>
      </header>

      {/* Body */}
      <div className="flex-1 flex overflow-hidden relative">
        <aside
          className={`
            absolute md:static inset-y-0 left-0 z-10
            w-72 md:w-80 bg-surface border-r border-outline-soft
            transform transition-transform duration-200 ease-out
            ${drawerOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0"}
          `}
        >
          <HistorySidebar
            activeSession={activeSession}
            onSelect={(s) => { setActiveSession(s); setDrawerOpen(false); }}
            onNew={() => { setActiveSession(null); setDrawerOpen(false); }}
          />
        </aside>

        {drawerOpen && (
          <div
            className="md:hidden absolute inset-0 bg-black/40 z-0"
            onClick={() => setDrawerOpen(false)}
            aria-hidden
          />
        )}

        <main className="flex-1 overflow-hidden">
          <ChatStream key={activeSession?.id ?? "new"} session={activeSession} />
        </main>
      </div>
    </div>
  );
}
