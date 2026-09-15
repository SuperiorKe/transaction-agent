import { useState } from "react";

import type { components } from "../api/openapi.gen";
import { formatAgo } from "../time";
import { useTick } from "../useTick";

export type AuditEvent = components["schemas"]["AuditEventView"];

const MAX_VALUE_CHARS = 60;

// Raw event types and a short payload summary, with no label map to keep in sync with
// app/audit.py (eng review D9). A new event type shows up correctly with no UI change.
function summarise(event: AuditEvent): string {
  const { payload } = event;
  if (event.type === "status.changed" && typeof payload.from === "string" && typeof payload.to === "string") {
    return `${payload.from} → ${payload.to}`;
  }
  return Object.entries(payload)
    .map(([key, value]) => `${key}=${truncate(typeof value === "string" ? value : JSON.stringify(value))}`)
    .join(" ");
}

function truncate(text: string): string {
  return text.length > MAX_VALUE_CHARS ? `${text.slice(0, MAX_VALUE_CHARS)}…` : text;
}

export function AuditLog({ events }: { events: readonly AuditEvent[] }) {
  const [open, setOpen] = useState(false);
  useTick(15_000);
  const now = Date.now();
  const count = `${events.length} ${events.length === 1 ? "event" : "events"}`;

  return (
    <section className="audit">
      <button
        type="button"
        className="audit-toggle"
        aria-expanded={open}
        onClick={() => setOpen((isOpen) => !isOpen)}
      >
        <span aria-hidden="true">{open ? "▾ " : "▸ "}</span>
        Audit log · {count}
      </button>
      {open &&
        (events.length === 0 ? (
          <p className="audit-empty">No events yet</p>
        ) : (
          <ol className="audit-rows">
            {events.map((event, index) => (
              <li key={`${event.at}-${index}`} className="audit-row">
                <code>{event.type}</code>
                <span className="audit-time">{formatAgo(event.at, now)}</span>
                <span className="audit-summary">{summarise(event)}</span>
              </li>
            ))}
          </ol>
        ))}
    </section>
  );
}
