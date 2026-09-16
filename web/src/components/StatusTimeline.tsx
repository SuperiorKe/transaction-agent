import type { StageView, TxStatus } from "../stages";
import { reachedStages } from "../stages";
import { formatAgo } from "../time";
import { RELATIVE_TIME_TICK_MS, useTick } from "../useTick";

export interface StatusTimelineProps {
  status: TxStatus;
  statusHistory: readonly TxStatus[];
  /** Newest audit event's `at`, or null before there are any. */
  lastUpdateAt: string | null;
}

type Icon = "check" | "dot" | "cross";

const MARK_WORD: Record<StageView["mark"], string> = {
  reached: "reached",
  current: "current",
  skipped: "skipped",
  future: "upcoming",
};

// Colour is never the only signal (DESIGN.md): every state also has an icon or a word.
function chip(stage: StageView): { icon: Icon | null; text: string; state: string } {
  if (stage.mark === "current" && stage.outcome) {
    switch (stage.outcome) {
      case "approved":
        return { icon: "check", text: "Approved", state: "approved" };
      case "confirmed":
        return { icon: "check", text: stage.label, state: "confirmed" };
      case "closed":
        return { icon: null, text: "Closed", state: "closed" };
      case "failed":
        return { icon: "cross", text: "Failed", state: "failed" };
    }
  }
  const icon = stage.mark === "reached" ? "check" : stage.mark === "current" ? "dot" : null;
  return { icon, text: stage.label, state: MARK_WORD[stage.mark] };
}

// Inline SVG rather than ✓ ● ✕ text: IBM Plex doesn't include those glyphs, so text marks would
// fall back to whatever font the demo laptop has (DESIGN.md "Type").
function StageIcon({ icon }: { icon: Icon }) {
  return (
    <svg
      className="stage-icon"
      data-icon={icon}
      viewBox="0 0 16 16"
      width="1em"
      height="1em"
      aria-hidden="true"
      focusable="false"
    >
      {icon === "check" && (
        <path
          d="M3 8.5l3.2 3L13 4.5"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      )}
      {icon === "dot" && <circle cx="8" cy="8" r="4" fill="currentColor" />}
      {icon === "cross" && (
        <path
          d="M4 4l8 8M12 4l-8 8"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
        />
      )}
    </svg>
  );
}

export function StatusTimeline({ status, statusHistory, lastUpdateAt }: StatusTimelineProps) {
  useTick(RELATIVE_TIME_TICK_MS);
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
          const { icon, text, state } = chip(stage);
          return (
            <li
              key={stage.stage}
              className="stage"
              data-mark={stage.mark}
              data-outcome={stage.outcome}
              aria-label={`${stage.label}: ${state}`}
            >
              {icon && <StageIcon icon={icon} />}
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
