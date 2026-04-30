import { useState } from "react";
import HistorySidebar from "./components/HistorySidebar.jsx";
import ChatStream from "./components/ChatStream.jsx";
import BriefingsPane from "./components/BriefingsPane.jsx";
import TracePane from "./components/TracePane.jsx";
import EnvSwitcher from "./components/EnvSwitcher.jsx";
import SystemSwitcher from "./components/SystemSwitcher.jsx";
import { getSession } from "./lib/api.js";

export default function App() {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [activeSession, setActiveSession] = useState(null);
  // Bumped after each completed turn so the sidebar refetches the session list.
  const [sessionVersion, setSessionVersion] = useState(0);
  // Bumped on every "+ New investigation" click. Used in the ChatStream key so
  // a fresh mount happens even if activeSession was already null (i.e. user
  // clicks "new" right after finishing a chat — without this, the key
  // wouldn't change and the old conversation would remain on screen).
  const [newChatNonce, setNewChatNonce] = useState(0);
  // Active deployment env (ppe / stage / future prod). EnvSwitcher initializes
  // this from localStorage / backend default; ChatStream sends it on every
  // request so the right kubeconfig + db creds are used.
  const [activeEnv, setActiveEnv] = useState("");
  // Active DB system filter (mssql / postgres). Scopes RAG retrieval to
  // the matching corpus subset so PG-only docs don't pollute MSSQL answers.
  const [activeSystem, setActiveSystem] = useState("");
  // Which top-level surface is shown on the right: chat | briefings | trace.
  // Sidebar stays visible across all modes so chat history is always reachable.
  const [mode, setMode] = useState("chat");

  async function handleSelect(stub) {
    try {
      const full = await getSession(stub.id);
      setActiveSession(full);
    } catch {
      setActiveSession(stub);
    }
    setMode("chat");
    setDrawerOpen(false);
  }

  function handleNew() {
    setActiveSession(null);
    setNewChatNonce((n) => n + 1);
    setMode("chat");
    setDrawerOpen(false);
  }

  function handleDeleted(deletedId) {
    // If the deleted session (or "all") was the one on screen, drop it +
    // remount ChatStream so the user lands on a fresh chat.
    if (deletedId === "__all__" || deletedId === activeSession?.id) {
      setActiveSession(null);
      setNewChatNonce((n) => n + 1);
    }
  }

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
        <div className="ml-auto flex items-center gap-2">
          <SystemSwitcher value={activeSystem} onChange={setActiveSystem} />
          <EnvSwitcher value={activeEnv} onChange={setActiveEnv} />
        </div>
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
            onSelect={handleSelect}
            onNew={handleNew}
            onDeleted={handleDeleted}
            refreshKey={sessionVersion}
          />
        </aside>

        {drawerOpen && (
          <div
            className="md:hidden absolute inset-0 bg-black/40 z-0"
            onClick={() => setDrawerOpen(false)}
            aria-hidden
          />
        )}

        <main className="flex-1 overflow-hidden flex flex-col">
          {/* Mode tabs */}
          <nav className="flex items-center gap-1 px-3 pt-2 border-b border-outline-soft bg-surface/40">
            {[
              { id: "chat", label: "Chat" },
              { id: "briefings", label: "Briefings" },
              { id: "trace", label: "Trace" },
            ].map((t) => (
              <button
                key={t.id}
                onClick={() => setMode(t.id)}
                className={`
                  px-3 py-1.5 text-xs font-tech rounded-t transition relative
                  ${mode === t.id
                    ? "bg-surface text-primary border border-outline-soft border-b-surface"
                    : "text-ink-muted hover:text-ink hover:bg-surface-2"}
                `}
              >
                {t.label}
              </button>
            ))}
          </nav>

          <div className="flex-1 overflow-hidden">
            {mode === "chat" && (
              <ChatStream
                key={activeSession?.id ?? `new-${newChatNonce}`}
                session={activeSession}
                env={activeEnv}
                system={activeSystem}
                onTurnComplete={() => setSessionVersion((v) => v + 1)}
              />
            )}
            {mode === "briefings" && (
              <BriefingsPane env={activeEnv} system={activeSystem} />
            )}
            {mode === "trace" && (
              <TracePane env={activeEnv} system={activeSystem} />
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
