import { useState } from "react";

import type { components } from "../api/openapi.gen";
import { formatAgo } from "../time";
import { RELATIVE_TIME_TICK_MS, useTick } from "../useTick";

export type AuditEvent = components["schemas"]["AuditEventView"];

const MAX_VALUE_CHARS = 60;
// The API sends only the newest events (`.limit(50)` in app/routes/transactions.py::_build_view),
// so a full list means "latest 50", not "50 in total".
export const AUDIT_LIMIT = 50;

// Raw event types and a short payload summary, with no label map to keep in sync with
// app/audit.py. A new event type shows up correctly with no UI change.
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

function countLabel(count: number): string {
  if (count >= AUDIT_LIMIT) return `latest ${AUDIT_LIMIT} events`;
  return `${count} ${count === 1 ? "event" : "events"}`;
}

// An SVG chevron: IBM Plex has no ▸ ▾ glyphs, and the fallback font sat below the label's baseline.
function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      className="audit-chevron"
      data-open={open}
      viewBox="0 0 16 16"
      width="0.8em"
      height="0.8em"
      aria-hidden="true"
      focusable="false"
    >
      <path
        d={open ? "M3 6l5 5 5-5" : "M6 3l5 5-5 5"}
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function AuditLog({ events }: { events: readonly AuditEvent[] }) {
  const [open, setOpen] = useState(false);
  useTick(RELATIVE_TIME_TICK_MS);
  const now = Date.now();

  return (
    <section className="audit">
      <button
        type="button"
        className="audit-toggle"
        aria-expanded={open}
        onClick={() => setOpen((isOpen) => !isOpen)}
      >
        <Chevron open={open} />
        Audit log · {countLabel(events.length)}
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
