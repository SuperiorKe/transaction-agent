import type { ReactNode } from "react";

import type { TransactionView } from "../api/client";
import { useTick } from "../useTick";

type Negotiation = TransactionView["negotiations"][number];

// DESIGN.md: "a ticking timer off answered_at". 1 s, not the 15 s RELATIVE_TIME_TICK_MS
// StatusTimeline uses for "last update N ago" -- a live call duration reads as ticking only at
// whole seconds.
const CALL_TICK_MS = 1000;

/**
 * The API returns negotiations oldest first. Keeping that ordering here makes a confirmation
 * call naturally replace the earlier price-negotiation call without the UI inventing state.
 */
export function CallPanel({ view }: { view: TransactionView }) {
  // Called unconditionally, before the early return below, so this hook always runs in the same
  // order across renders regardless of whether a call exists yet.
  useTick(CALL_TICK_MS);
  const call = view.negotiations.at(-1);
  if (!call) {
    return (
      <section className="card call-panel" aria-labelledby="call-heading">
        <h2 id="call-heading">Latest call</h2>
        <p className="call-empty">No provider call has started yet.</p>
      </section>
    );
  }

  return (
    <section className="card call-panel" aria-labelledby="call-heading">
      <div className="card-heading">
        <h2 id="call-heading">Latest call</h2>
        <span className="call-kind">{call.kind === "confirmation" ? "Confirmation" : "Negotiation"}</span>
      </div>
      <dl className="call-facts">
        <div><dt>Provider</dt><dd className="call-provider-name">{call.provider_name}</dd></div>
        {view.current_provider && <div><dt>Number</dt><dd>{view.current_provider.phone_masked}</dd></div>}
        <div><dt>State</dt><dd>{humanise(call.status)}</dd></div>
        <div><dt>Duration</dt><dd>{formatDuration(call)}</dd></div>
        <div><dt>Counteroffers</dt><dd>{call.attempt_count}</dd></div>
      </dl>
      <div className="transcript-wrap">
        <h3>Transcript</h3>
        {call.transcript.length ? (
          <ol className="transcript" aria-label="Call transcript">
            {call.transcript.map((turn, index) => (
              <li key={`${turn.at}-${index}`} className={`transcript-turn transcript-${turn.speaker}`}>
                {turn.speaker !== "system" && (
                  <span className="transcript-speaker">{humanise(turn.speaker)}</span>
                )}
                <p>{highlightRecordedAmounts(turn.text, call)}</p>
              </li>
            ))}
          </ol>
        ) : (
          <p className="call-empty">The call has no transcript yet.</p>
        )}
      </div>
    </section>
  );
}

/**
 * duration_seconds is the persisted, frozen truth once the call has an outcome. While it's still
 * null, this ticks live off answered_at -- but only while the negotiation is actually IN_PROGRESS:
 * a missed end callback (answered_at set, duration_seconds never computed) must freeze at "In
 * progress" rather than tick forever (DESIGN.md, CallPanel contract).
 */
function formatDuration(call: Negotiation): string {
  if (call.duration_seconds !== null) return mmss(call.duration_seconds);
  const live = call.answered_at !== null && call.status === "IN_PROGRESS";
  if (!live) return "In progress";
  const elapsedMs = Date.now() - Date.parse(call.answered_at as string);
  return mmss(Math.max(0, Math.floor(elapsedMs / 1000)));
}

function mmss(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}

function humanise(value: string): string {
  return value.replaceAll("_", " ").toLowerCase().replace(/\b\w/g, (letter) => letter.toUpperCase());
}

/**
 * This deliberately returns React text nodes and <mark>s, never HTML. Numeric text is highlighted
 * only when its normalised value exists in this call's persisted offers; a budget mentioned in the
 * conversation is therefore not made to look like a provider quote.
 */
export function highlightRecordedAmounts(text: string, call: Negotiation) {
  const amounts = new Set(call.offers.flatMap((offer) => (offer.amount === null ? [] : [offer.amount])));
  const chunks: ReactNode[] = [];
  const pattern = /(?:KES\s*)?(\d[\d,]*)/gi;
  let cursor = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text)) !== null) {
    const amount = Number((match[1] ?? "").replaceAll(",", ""));
    if (!amounts.has(amount)) continue;
    if (match.index > cursor) chunks.push(text.slice(cursor, match.index));
    chunks.push(<mark key={`${match.index}-${match[0]}`}>{match[0]}</mark>);
    cursor = pattern.lastIndex;
  }
  if (cursor < text.length) chunks.push(text.slice(cursor));
  return chunks.length ? chunks : text;
}
