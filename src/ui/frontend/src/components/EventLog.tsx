import { useEffect, useState } from "react";

export interface LogLine {
  ts: string;
  level: "info" | "warn" | "error" | "success";
  msg: string;
}

export default function EventLog({ lines }: { lines: LogLine[] }) {
  // Persisted across gate transitions and reloads (survives the whole run).
  const [open, setOpen] = useState<boolean>(
    () => localStorage.getItem("me-log-open") !== "false",
  );

  useEffect(() => {
    localStorage.setItem("me-log-open", String(open));
  }, [open]);

  return (
    <div className="card">
      <button
        className="log-header"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span className={`log-caret ${open ? "open" : ""}`}>▶</span>
        <span className="section-label" style={{ margin: 0 }}>
          Activity log
        </span>
        <span className="log-count">{lines.length}</span>
      </button>
      {open && (
        <div className="event-log" style={{ marginTop: 10 }}>
          {lines.length === 0 ? (
            <div className="line muted">No activity yet.</div>
          ) : (
            lines.map((l, i) => (
              <div key={i} className={`line ${l.level}`}>
                <span className="ts">{l.ts}</span>
                <span className="msg">{l.msg}</span>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
