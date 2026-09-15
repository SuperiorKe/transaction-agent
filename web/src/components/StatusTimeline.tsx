import type { StageView, TxStatus } from "../stages";
import { reachedStages } from "../stages";
import { formatAgo } from "../time";
import { useTick } from "../useTick";

export interface StatusTimelineProps {
  status: TxStatus;
  statusHistory: readonly TxStatus[];
  /** Newest audit event's `at`, or null before there are any. */
  lastUpdateAt: string | null;
}

const MARK_WORD: Record<StageView["mark"], string> = {
  reached: "reached",
  current: "current",
  skipped: "skipped",
  future: "upcoming",
};

// Colour is never the only signal (DESIGN.md): every state also has a glyph or a word.
function chip(stage: StageView): { glyph: string; text: string; state: string } {
  if (stage.mark === "current" && stage.outcome) {
    switch (stage.outcome) {
      case "confirmed":
        return { glyph: "✓", text: stage.label, state: "confirmed" };
      case "closed":
        return { glyph: "", text: "Closed", state: "closed" };
      case "failed":
        return { glyph: "✕", text: "Failed", state: "failed" };
    }
  }
  const glyph = stage.mark === "reached" ? "✓" : stage.mark === "current" ? "●" : "";
  return { glyph, text: stage.label, state: MARK_WORD[stage.mark] };
}

export function StatusTimeline({ status, statusHistory, lastUpdateAt }: StatusTimelineProps) {
  useTick(15_000);
  const stages = reachedStages(status, statusHistory);
  const outcome = stages.find((stage) => stage.mark === "current")?.outcome;
  const ago = lastUpdateAt ? formatAgo(lastUpdateAt, Date.now()) : "";

  return (
    <header className="timeline">
      <div className="brand">
        <span className="brand-dot" aria-hidden="true" />
        Transaction Agent
      </div>
      <ol className="stages">
        {stages.map((stage) => {
          const { glyph, text, state } = chip(stage);
          return (
            <li
              key={stage.stage}
              className="stage"
              data-mark={stage.mark}
              data-outcome={stage.outcome}
              aria-label={`${stage.label}: ${state}`}
            >
              {glyph && (
                <span className="stage-glyph" aria-hidden="true">
                  {glyph}
                </span>
              )}
              {text}
            </li>
          );
        })}
      </ol>
      <p className="status-line" data-outcome={outcome}>
        <span className="status-raw">{status}</span>
        {ago && (
          <>
            {" · "}
            <span>last update {ago}</span>
          </>
        )}
      </p>
    </header>
  );
}
