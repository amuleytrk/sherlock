import { useEffect, useRef, useState } from "react";
import mermaid from "mermaid";

let initialized = false;

export default function MermaidBlock({ source }) {
  const ref = useRef(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    if (!initialized) {
      mermaid.initialize({
        startOnLoad: false,
        theme: "dark",
        securityLevel: "strict",
        fontFamily: "Inter, sans-serif",
      });
      initialized = true;
    }
    let cancelled = false;
    setErr(null);
    (async () => {
      try {
        const id = "mmd_" + Math.random().toString(36).slice(2);
        const { svg } = await mermaid.render(id, source);
        if (!cancelled && ref.current) ref.current.innerHTML = svg;
      } catch (e) {
        if (!cancelled) setErr(String(e?.message || e));
      }
    })();
    return () => { cancelled = true; };
  }, [source]);

  if (err) {
    return (
      <pre className="text-xs text-danger whitespace-pre-wrap p-2 bg-surface-3 rounded">
        Mermaid render error: {err}
      </pre>
    );
  }
  return <div ref={ref} className="overflow-x-auto -mx-3 px-3" />;
}
