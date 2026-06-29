/** Thin REST client. Vite proxies /api/* to FastAPI on :8000. */

export async function getHealth() {
  const r = await fetch("/api/health");
  if (!r.ok) throw new Error(`/health → ${r.status}`);
  return r.json();
}

export async function listEnvs() {
  const r = await fetch("/api/envs");
  if (!r.ok) return { default: "ppe", envs: [] };
  return r.json();
}

export async function listSessions() {
  const r = await fetch("/api/sessions");
  if (!r.ok) return { sessions: [] };
  return r.json();
}

export async function getSession(sessionId) {
  const r = await fetch(`/api/sessions/${sessionId}`);
  if (!r.ok) throw new Error(`/sessions/${sessionId} → ${r.status}`);
  return r.json();
}

export async function deleteSession(sessionId) {
  const r = await fetch(`/api/sessions/${sessionId}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`DELETE /sessions/${sessionId} → ${r.status}`);
  return r.json();
}

export async function deleteAllSessions() {
  const r = await fetch(`/api/sessions`, { method: "DELETE" });
  if (!r.ok) throw new Error(`DELETE /sessions → ${r.status}`);
  return r.json();
}

export async function listBriefings() {
  const r = await fetch("/api/briefings");
  if (!r.ok) return { briefings: [] };
  return r.json();
}

export async function getBriefing(id) {
  const r = await fetch(`/api/briefings/${id}`);
  if (!r.ok) throw new Error(`/briefings/${id} → ${r.status}`);
  return r.json();
}

export async function runBriefing({ env = null } = {}) {
  const r = await fetch("/api/briefings/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ env }),
  });
  if (!r.ok) throw new Error(`POST /briefings/run → ${r.status}`);
  return r.json();
}

export async function getRca(rcaId) {
  const r = await fetch(`/api/rca/${rcaId}`);
  if (!r.ok) throw new Error(`/rca/${rcaId} → ${r.status}`);
  return r.json();
}

export async function getAuditTrail(rcaId) {
  const r = await fetch(`/api/rca/${rcaId}/audit`);
  if (!r.ok) return { entries: [] };
  return r.json();
}

export function artifactUrl(path) {
  return `/api/artifacts?path=${encodeURIComponent(path)}`;
}
