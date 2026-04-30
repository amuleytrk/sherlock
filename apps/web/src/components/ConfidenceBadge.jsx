import { useState } from "react";

/**
 * ConfidenceBadge — displays the trust-layer aggregate score and an
 * expandable per-claim breakdown.
 *
 * Bands map to the dark theme's success/warn/danger tokens. Below 60 we
 * surface a pinned "verify before acting" warning above the answer.
 */
const BAND_STYLES = {
  green: {
    chip: "bg-success/15 text-success border-success/40",
    dot: "bg-success",
    label: "high",
  },
  yellow: {
    chip: "bg-warn/15 text-warn border-warn/40",
    dot: "bg-warn",
    label: "moderate",
  },
  red: {
    chip: "bg-danger/15 text-danger border-danger/40",
    dot: "bg-danger",
    label: "low",
  },
};

export default function ConfidenceBadge({ verification }) {
  const [expanded, setExpanded] = useState(false);
  if (!verification) return null;
  const { score, band, claims = [], extracted_count, note } = verification;
  const style = BAND_STYLES[band] || BAND_STYLES.yellow;

  const supportedCount = claims.filter((c) => c.supported).length;
  const totalClaims = claims.length;

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <button
          onClick={() => setExpanded((v) => !v)}
          className={`
            inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-[11px] font-tech
            ${style.chip} hover:opacity-90 transition
          `}
          aria-expanded={expanded}
          title={note || `${score}% confidence`}
        >
          <span className={`inline-block w-1.5 h-1.5 rounded-full ${style.dot}`} />
          <span>confidence: <strong>{score}%</strong></span>
          <span className="opacity-70">·</span>
          <span>{style.label}</span>
          {totalClaims > 0 && (
            <>
              <span className="opacity-70">·</span>
              <span>{supportedCount}/{totalClaims} claims verified</span>
            </>
          )}
          <svg width="9" height="9" viewBox="0 0 10 10" className={`opacity-70 transition ${expanded ? "rotate-180" : ""}`}>
            <path d="M2 4l3 3 3-3" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round" />
          </svg>
        </button>
        {note && (
          <span className="text-[11px] font-tech text-ink-muted">{note}</span>
        )}
      </div>

      {band === "red" && (
        <div className="px-3 py-2 rounded bg-danger/10 border border-danger/40 text-danger text-xs">
          ⚠ Low confidence — verify any specific endpoint, table, or flag against
          the source code before acting on this answer.
        </div>
      )}

      {expanded && totalClaims > 0 && (
        <div className="rounded border border-outline-soft overflow-hidden">
          <table className="w-full text-[11px] font-tech">
            <thead>
              <tr className="bg-surface-2/40 text-ink-muted">
                <th className="text-left px-2 py-1.5">claim</th>
                <th className="text-left px-2 py-1.5 w-20">kind</th>
                <th className="text-left px-2 py-1.5 w-14">score</th>
                <th className="text-left px-2 py-1.5">evidence</th>
              </tr>
            </thead>
            <tbody>
              {claims.map((c, i) => {
                const claimBand =
                  c.score >= 80 ? "green"
                  : c.score >= 50 ? "yellow"
                  : "red";
                const cs = BAND_STYLES[claimBand];
                return (
                  <tr key={i} className="border-t border-outline-soft/40">
                    <td className="px-2 py-1.5 text-primary">
                      {c.text}
                    </td>
                    <td className="px-2 py-1.5 text-ink-muted">{c.kind}</td>
                    <td className="px-2 py-1.5">
                      <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded border ${cs.chip}`}>
                        <span className={`w-1 h-1 rounded-full ${cs.dot}`} />
                        {c.score}
                      </span>
                    </td>
                    <td className="px-2 py-1.5 text-ink-dim italic">
                      {c.evidence_excerpt
                        ? `"${c.evidence_excerpt.slice(0, 100)}${c.evidence_excerpt.length > 100 ? "…" : ""}"`
                        : c.supported ? "(supported but excerpt unavailable)" : "(not found in chunks)"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {expanded && totalClaims === 0 && extracted_count === 0 && (
        <div className="text-[11px] font-tech text-ink-muted px-3 py-2 rounded bg-surface-2/40 border border-outline-soft">
          No HTTP endpoints, SQL tables, or feature-flag references in this answer
          to verify. Confidence reported as the neutral baseline.
        </div>
      )}
    </div>
  );
}
