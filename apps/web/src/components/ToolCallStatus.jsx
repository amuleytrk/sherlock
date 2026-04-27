export default function ToolCallStatus({ phase, message, live = false }) {
  return (
    <div className={`flex items-center gap-2 text-xs label-caps ${live ? "text-primary" : "text-ink-muted"}`}>
      <span className={`inline-block w-2 h-2 rounded-full ${live ? "bg-primary animate-pulse-soft" : "bg-ink-muted"}`} />
      <span>{phase}</span>
      <span className="font-tech text-ink-dim normal-case tracking-normal text-[11px] truncate">{message}</span>
    </div>
  );
}
