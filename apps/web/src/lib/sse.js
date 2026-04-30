import { fetchEventSource } from "@microsoft/fetch-event-source";

/**
 * Stream chat events from the backend.
 *
 * @param {object} args
 * @param {string} args.message
 * @param {string|null} [args.sessionId]
 * @param {string|null} [args.env]    Active env (e.g. "ppe", "stage"). null → backend default.
 * @param {string|null} [args.system] DB-system filter ("mssql" | "postgres"). null → no filter.
 * @param {(name: string, data: object) => void} args.onEvent
 * @param {AbortSignal} [args.signal]
 * @returns {Promise<void>}
 */
export async function streamChat({ message, sessionId = null, env = null, system = null, onEvent, signal }) {
  await fetchEventSource("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id: sessionId, env, system }),
    signal,
    openWhenHidden: true,
    onmessage(ev) {
      let data = {};
      try { data = JSON.parse(ev.data); } catch { /* keep empty */ }
      onEvent(ev.event || "message", data);
    },
    onerror(err) {
      onEvent("error", { error: String(err) });
      throw err; // stop retry
    },
  });
}
