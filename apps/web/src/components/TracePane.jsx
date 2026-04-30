import { useState, useRef } from "react";
import { fetchEventSource } from "@microsoft/fetch-event-source";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import MermaidBlock from "./MermaidBlock.jsx";

/**
 * TracePane — cross-service request trace.
 *
 * User pastes an identifier (qrcode / tape_id / correlation_id) → backend
 * discovers the candidate service list, fetches logs from each in parallel,
 * stitches by identifier + propagated correlation_ids, renders a Mermaid
 * sequenceDiagram. Failure points are highlighted.
 */
const SUGGESTED_TIME_WINDOWS = [
  { label: "10 min", seconds: 600 },
  { label: "1 hour", seconds: 3600 },
  { label: "6 hours", seconds: 21600 },
  { label: "12 hours", seconds: 43200 },
  { label: "24 hours", seconds: 86400 },
];

export default function TracePane({ env, system }) {
  const [identifier, setIdentifier] = useState("");
  const [windowSec, setWindowSec] = useState(3600);
  const [hint, setHint] = useState("");
  const [running, setRunning] = useState(false);
  const [events, setEvents] = useState([]);   // SSE events for the timeline ribbon
  const [pipeline, setPipeline] = useState(null);
  const [stitched, setStitched] = useState(null);
  const [mermaid, setMermaid] = useState("");
  const [narrative, setNarrative] = useState("");
  const [done, setDone] = useState(null);     // final stats
  const [error, setError] = useState(null);
  const [selectedEventIdx, setSelectedEventIdx] = useState(null);
  const abortRef = useRef(null);

  function reset() {
    setEvents([]);
    setPipeline(null);
    setStitched(null);
    setMermaid("");
    setNarrative("");
    setDone(null);
    setError(null);
    setSelectedEventIdx(null);
  }

  async function start(e) {
    e?.preventDefault?.();
    if (!identifier.trim() || running) return;
    reset();
    setRunning(true);

    const ac = new AbortController();
    abortRef.current = ac;

    try {
      await fetchEventSource("/api/trace", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          identifier: identifier.trim(),
          env: env || null,
          system: system || null,
          since_seconds: windowSec,
          hint: hint || null,
        }),
        signal: ac.signal,
        openWhenHidden: true,
        onmessage(ev) {
          let data = {};
          try { data = JSON.parse(ev.data); } catch { /* keep empty */ }
          setEvents((es) => [...es, { name: ev.event || "message", data }]);
          if (ev.event === "pipeline") setPipeline(data);
          if (ev.event === "stitched") setStitched(data);
          if (ev.event === "mermaid") setMermaid(data.diagram || "");
          if (ev.event === "narrative") setNarrative(data.text || "");
          if (ev.event === "trace_done") setDone(data);
        },
        onerror(err) {
          setError(String(err));
          throw err;
        },
      });
    } catch {
      // already surfaced
    } finally {
      setRunning(false);
    }
  }

  function cancel() {
    abortRef.current?.abort();
    setRunning(false);
  }

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Form */}
      <form
        onSubmit={start}
        className="px-4 py-3 border-b border-outline-soft bg-surface/40 flex flex-wrap items-center gap-2"
      >
        <input
          value={identifier}
          onChange={(e) => setIdentifier(e.target.value)}
          placeholder="qrcode (e.g. 9E-070524-N29401), tape_id (12 hex), or correlation_id…"
          className="flex-1 min-w-[260px] px-3 py-2 rounded bg-surface-2 border border-outline-soft focus:border-primary outline-none text-sm font-tech"
          disabled={running}
        />
        <select
          value={windowSec}
          onChange={(e) => setWindowSec(Number(e.target.value))}
          className="px-2 py-2 rounded bg-surface-2 border border-outline-soft text-xs font-tech"
          disabled={running}
        >
          {SUGGESTED_TIME_WINDOWS.map((w) => (
            <option key={w.seconds} value={w.seconds}>{w.label}</option>
          ))}
        </select>
        <select
          value={hint}
          onChange={(e) => setHint(e.target.value)}
          className="px-2 py-2 rounded bg-surface-2 border border-outline-soft text-xs font-tech"
          disabled={running}
          title="Pipeline hint — leave 'auto' to detect from identifier shape"
        >
          <option value="">flow: auto</option>
          <option value="milestone">flow: milestone</option>
          <option value="device_event">flow: device event</option>
        </select>
        {running ? (
          <button type="button" onClick={cancel}
                  className="px-4 py-2 rounded bg-surface-3 text-ink hover:bg-surface-2 text-sm">
            Cancel
          </button>
        ) : (
          <button type="submit" disabled={!identifier.trim()}
                  className="px-4 py-2 rounded bg-primary text-primary-fg font-medium disabled:opacity-40 text-sm">
            Trace
          </button>
        )}
      </form>

      {/* Body */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
        {error && (
          <div className="px-3 py-2 rounded bg-danger/10 border border-danger/40 text-danger text-sm">
            {error}
          </div>
        )}

        {events.length === 0 && !running && (
          <Welcome env={env} />
        )}

        {/* Status ribbon — every status / pipeline / fetched event */}
        {events.length > 0 && (
          <div className="space-y-1.5">
            {events.map((ev, i) => (
              <RibbonRow key={i} ev={ev} />
            ))}
          </div>
        )}

        {pipeline && (
          <div className="rounded border border-outline-soft p-3 bg-surface-2/30">
            <div className="label-caps mb-1">pipeline</div>
            <div className="text-sm">{pipeline.flow}</div>
            <div className="text-xs text-ink-muted mt-1">{pipeline.rationale}</div>
            <div className="mt-2 flex flex-wrap gap-1">
              {(pipeline.services || []).map((s) => (
                <span key={s} className="px-2 py-0.5 rounded bg-surface-3 text-[11px] font-tech text-ink-dim">{s}</span>
              ))}
            </div>
          </div>
        )}

        {mermaid && (
          <div className="rounded border border-outline-soft p-3 bg-surface-2/20">
            <div className="label-caps mb-2">timeline</div>
            <MermaidBlock source={mermaid} />
          </div>
        )}

        {narrative && (
          <div className="rounded border border-primary/30 p-3 bg-primary/5">
            <div className="label-caps text-primary mb-1">narrative</div>
            <div className="text-sm prose-invert max-w-none">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{narrative}</ReactMarkdown>
            </div>
          </div>
        )}

        {stitched && stitched.events && stitched.events.length > 0 && (
          <EventTable
            events={stitched.events}
            failureIdx={stitched.failure_event_idx}
            selectedIdx={selectedEventIdx}
            onSelect={setSelectedEventIdx}
          />
        )}

        {selectedEventIdx !== null && stitched?.events?.[selectedEventIdx] && (
          <RawLogViewer event={stitched.events[selectedEventIdx]} onClose={() => setSelectedEventIdx(null)} />
        )}

        {done && (
          <div className="text-xs label-caps text-success">
            ✓ trace complete · {done.events} events · {done.services} services
            · fetch {done.fetch_ms}ms · total {done.total_ms}ms
            {done.failure_detected && " · ⚠ failure detected"}
          </div>
        )}
      </div>
    </div>
  );
}


function Welcome({ env }) {
  return (
    <div className="max-w-xl mx-auto py-8 space-y-3">
      <h2 className="mb-2">Cross-service trace</h2>
      <p className="text-ink-dim text-sm">
        Paste an identifier — Sherlock fans out kubectl logs across the candidate
        services in parallel, stitches the timeline by identifier and propagated
        correlation_ids, and renders the entire request flow as a Mermaid
        sequence diagram. Errors are highlighted in red.
      </p>
      <div className="text-xs text-ink-muted">Currently scoped to env: <span className="font-tech text-primary">{env || "?"}</span></div>
      <ul className="space-y-1.5 text-sm text-ink-dim">
        <li className="rounded bg-surface-2 border border-outline-soft px-3 py-2">
          <span className="font-tech text-primary">9E-070524-N29401</span> — qrcode → milestone flow
        </li>
        <li className="rounded bg-surface-2 border border-outline-soft px-3 py-2">
          <span className="font-tech text-primary">D18C6EDE8E62</span> — tape_id → device-event flow
        </li>
        <li className="rounded bg-surface-2 border border-outline-soft px-3 py-2">
          <span className="font-tech text-primary">8680093d-b639-...</span> — correlation_id → broad sweep
        </li>
      </ul>
    </div>
  );
}


function RibbonRow({ ev }) {
  const { name, data } = ev;
  if (name === "trace_started") {
    return <div className="text-xs label-caps">→ trace started · <span className="text-primary normal-case font-tech">{data.identifier}</span> · env <span className="text-ink-muted normal-case font-tech">{data.env}</span></div>;
  }
  if (name === "status") {
    return <div className="text-xs label-caps text-ink-muted">· {data.phase}: <span className="normal-case font-tech text-ink-dim">{data.msg}</span></div>;
  }
  if (name === "logs_fetched") {
    return (
      <div className="text-xs label-caps text-success">
        ✓ logs fetched in {data.duration_ms}ms · {data.services_with_logs}/{data.services_total} services responded
      </div>
    );
  }
  if (name === "stitched") {
    return <div className="text-xs label-caps text-success">✓ stitched · {data.events?.length || 0} events · <span className="normal-case font-tech text-ink-dim">{data.summary}</span></div>;
  }
  return null;
}


function EventTable({ events, failureIdx, selectedIdx, onSelect }) {
  return (
    <div className="rounded border border-outline-soft overflow-hidden">
      <div className="px-3 py-2 label-caps bg-surface-2/40">events ({events.length})</div>
      <table className="w-full text-xs font-tech">
        <thead>
          <tr className="text-ink-muted bg-surface-2/30">
            <th className="text-left px-3 py-1.5">ts</th>
            <th className="text-left px-3 py-1.5">service</th>
            <th className="text-left px-3 py-1.5">message</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e, i) => {
            const isErr = e.is_error;
            const isFail = i === failureIdx;
            const isSelected = i === selectedIdx;
            return (
              <tr
                key={i}
                onClick={() => onSelect(i)}
                className={`
                  border-t border-outline-soft/40 cursor-pointer
                  ${isSelected ? "bg-surface-3" : "hover:bg-surface-2"}
                  ${isFail ? "bg-danger/10" : ""}
                  ${isErr && !isFail ? "text-danger" : ""}
                `}
              >
                <td className="px-3 py-1.5 text-ink-muted whitespace-nowrap">
                  {e.ts ? new Date(e.ts).toISOString().slice(11, 23) : "—"}
                </td>
                <td className="px-3 py-1.5 text-primary whitespace-nowrap">{e.service}</td>
                <td className="px-3 py-1.5 max-w-[600px] truncate" title={e.message}>
                  {isFail && "🔴 "}{e.message}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}


function RawLogViewer({ event, onClose }) {
  return (
    <div className="rounded border border-primary/40 bg-surface-2/40 overflow-hidden">
      <div className="flex items-center px-3 py-2 bg-surface-3/60">
        <span className="label-caps text-primary">selected event</span>
        <span className="ml-2 text-xs font-tech text-ink-muted">{event.service} · {event.correlation_id || "(no correlation_id)"}</span>
        <button onClick={onClose} className="ml-auto text-xs label-caps text-ink-muted hover:text-ink">close</button>
      </div>
      <pre className="px-3 py-2 text-[11px] font-tech text-ink-dim whitespace-pre-wrap break-all max-h-72 overflow-y-auto">
{event.message}
      </pre>
    </div>
  );
}
