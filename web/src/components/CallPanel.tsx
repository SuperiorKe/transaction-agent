import type { ReactNode } from "react";

import type { TransactionView } from "../api/client";

type Negotiation = TransactionView["negotiations"][number];

/**
 * The API returns negotiations oldest first. Keeping that ordering here makes a confirmation
 * call naturally replace the earlier price-negotiation call without the UI inventing state.
 */
export function CallPanel({ view }: { view: TransactionView }) {
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
        <div><dt>Provider</dt><dd>{call.provider_name}</dd></div>
        {view.current_provider && <div><dt>Number</dt><dd>{view.current_provider.phone_masked}</dd></div>}
        <div><dt>State</dt><dd>{humanise(call.status)}</dd></div>
        <div><dt>Duration</dt><dd>{formatDuration(call.duration_seconds)}</dd></div>
        <div><dt>Counteroffers</dt><dd>{call.attempt_count}</dd></div>
      </dl>
      <div className="transcript-wrap">
        <h3>Transcript</h3>
        {call.transcript.length ? (
          <ol className="transcript" aria-label="Call transcript">
            {call.transcript.map((turn, index) => (
              <li key={`${turn.at}-${index}`} className={`transcript-turn transcript-${turn.speaker}`}>
                <span className="transcript-speaker">{humanise(turn.speaker)}</span>
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

function formatDuration(seconds: number | null): string {
  if (seconds === null) return "In progress";
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
