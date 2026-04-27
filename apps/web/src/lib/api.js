/** Thin REST client. Vite proxies /api/* to FastAPI on :8000. */

export async function getHealth() {
  const r = await fetch("/api/health");
  if (!r.ok) throw new Error(`/health → ${r.status}`);
  return r.json();
}

export async function listSessions() {
  const r = await fetch("/api/sessions");
  if (!r.ok) return { sessions: [] };
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
