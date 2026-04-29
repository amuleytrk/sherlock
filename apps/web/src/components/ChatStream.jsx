import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { streamChat } from "../lib/sse.js";
import EvidenceCard from "./EvidenceCard.jsx";
import RcaReport from "./RcaReport.jsx";
import ThinkingIndicator from "./ThinkingIndicator.jsx";
import ToolCallStatus from "./ToolCallStatus.jsx";

export default function ChatStream({ session }) {
  const [messages, setMessages] = useState(session?.messages ?? []);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [trace, setTrace] = useState([]);
  const abortRef = useRef(null);
  const scrollRef = useRef(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, trace]);

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setMessages((ms) => [...ms, { role: "user", content: text }]);
    setBusy(true);
    setTrace([]);
    const collected = [];

    abortRef.current = new AbortController();
    try {
      await streamChat({
        message: text,
        sessionId: session?.id ?? null,
        signal: abortRef.current.signal,
        onEvent(name, data) {
          collected.push({ name, data });
          setTrace([...collected]);
        },
      });
    } catch {
      // already surfaced as an `error` event by sse.js
    } finally {
      setMessages((ms) => [...ms, { role: "agent", trace: collected }]);
      setTrace([]);
      setBusy(false);
    }
  }

  function cancel() {
    abortRef.current?.abort();
  }

  return (
    <div className="h-full flex flex-col">
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-6 space-y-6">
        {messages.length === 0 && !busy && <Welcome />}
        {messages.map((m, i) => (
          <MessageBubble key={i} m={m} />
        ))}
        {/* Render the live agent bubble whenever a request is in flight, even
            before the first SSE event arrives. The ThinkingIndicator inside
            the bubble (when live=true) fills the gap before the router event. */}
        {busy && <MessageBubble m={{ role: "agent", trace }} live />}
      </div>

      <form
        onSubmit={(e) => { e.preventDefault(); send(); }}
        className="border-t border-outline-soft p-3 flex gap-2 pb-[calc(0.75rem+env(safe-area-inset-bottom))]"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask Sherlock — API question or describe a bug…"
          className="flex-1 px-3 py-2 rounded bg-surface-2 border border-outline-soft focus:border-primary outline-none text-sm"
          disabled={busy}
          autoFocus
        />
        {busy ? (
          <button type="button" onClick={cancel}
                  className="px-4 py-2 rounded bg-surface-3 text-ink hover:bg-surface-2">
            Cancel
          </button>
        ) : (
          <button type="submit" disabled={!input.trim()}
                  className="px-4 py-2 rounded bg-primary text-primary-fg font-medium disabled:opacity-40">
            Send
          </button>
        )}
      </form>
    </div>
  );
}

function Welcome() {
  return (
    <div className="max-w-xl mx-auto py-8 space-y-5">
      <div>
        <h2 className="mb-2">Hi — I'm Sherlock.</h2>
        <p className="text-ink-dim text-sm">
          Ask me about Trackonomy APIs, feature flags, code patterns, or describe a bug
          and I'll investigate. I read from indexed code + docs, then PPE infrastructure
          read-only when you need a real RCA.
        </p>
      </div>
      <div>
        <span className="label-caps">try asking</span>
        <ul className="mt-2 space-y-1.5 text-sm text-ink-dim">
          <li className="rounded bg-surface-2 border border-outline-soft px-3 py-2">
            What's the API for labelling a white tape device?
          </li>
          <li className="rounded bg-surface-2 border border-outline-soft px-3 py-2">
            What does <span className="font-tech text-primary">feature_configuration.cross_customer_mesh_allowed</span> control?
          </li>
          <li className="rounded bg-surface-2 border border-outline-soft px-3 py-2">
            Device AABBCCDDEEFF events not appearing in lookup_parcels in PPE
          </li>
        </ul>
      </div>
    </div>
  );
}

function MessageBubble({ m, live = false }) {
  if (m.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] px-4 py-2 rounded-lg bg-surface-3 text-ink whitespace-pre-wrap">
          {m.content}
        </div>
      </div>
    );
  }
  // Agent turn — render trace events
  const events = m.trace ?? [];
  const finalRcaEvent = events.find((e) => e.name === "rca_done");

  // Accumulate streaming answer_delta tokens into a single markdown blob so
  // formatting (citations, code, lists) renders correctly. Filter the deltas
  // out of the per-event timeline since they're rendered as the blob below.
  const answerText = events
    .filter((e) => e.name === "answer_delta")
    .map((e) => e.data.text)
    .join("");
  const timelineEvents = events.filter((e) => e.name !== "answer_delta");

  // The thinking indicator is shown while the stream is in flight AND we
  // haven't started rendering the final answer yet. It disappears as soon
  // as the agent starts streaming tokens (Discovery) or finishes the RCA.
  const showThinking = live && !answerText && !finalRcaEvent;

  return (
    <div className="space-y-3">
      {timelineEvents.map((e, i) => (
        <TraceEvent key={i} event={e} live={live && i === timelineEvents.length - 1} />
      ))}
      {showThinking && <ThinkingIndicator />}
      {answerText && (
        <div className="prose-invert">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{answerText}</ReactMarkdown>
        </div>
      )}
      {finalRcaEvent && <RcaReport rcaId={finalRcaEvent.data.rca_id} />}
    </div>
  );
}

function TraceEvent({ event, live }) {
  const { name, data } = event;
  if (name === "router") {
    return (
      <div className="text-xs label-caps">
        Routed → <span className="text-primary">{data.intent}</span>
        {data.entities?.tape_id && (
          <span className="ml-2 font-tech normal-case tracking-normal text-ink-muted">
            · tape_id: {data.entities.tape_id}
          </span>
        )}
      </div>
    );
  }
  if (name === "status") {
    return <ToolCallStatus phase={data.phase} message={data.msg} live={live} />;
  }
  if (name === "tool_call") {
    const argsPreview = JSON.stringify(data.args || {}).slice(0, 120);
    return <ToolCallStatus phase="tool" message={`${data.name}(${argsPreview}…)`} live={live} />;
  }
  if (name === "tool_result") {
    return <EvidenceCard kind="raw" content={data.preview} />;
  }
  if (name === "evidence") {
    return <EvidenceCard kind={data.kind} content={data} />;
  }
  if (name === "agent_text") {
    return (
      <div className="prose-invert">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{data.text}</ReactMarkdown>
      </div>
    );
  }
  // answer_delta is intentionally NOT handled here — MessageBubble accumulates
  // tokens into a single markdown blob so formatting renders correctly.
  if (name === "rca_started") {
    return (
      <div className="text-xs label-caps text-primary">
        Investigation {data.rca_id} started · scratch:
        <span className="font-tech normal-case tracking-normal text-ink-muted ml-1">{data.scratch_dir}</span>
      </div>
    );
  }
  if (name === "rca_done") {
    return (
      <div className="text-xs label-caps text-success">
        ✓ RCA synthesized · {data.tool_calls} tool calls · {data.evidence_count} evidence files
      </div>
    );
  }
  if (name === "answer") {
    return (
      <div className="prose-invert">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{data.text || ""}</ReactMarkdown>
      </div>
    );
  }
  if (name === "error") {
    return <div className="text-sm text-danger">stream error: {data.error}</div>;
  }
  return null;
}
