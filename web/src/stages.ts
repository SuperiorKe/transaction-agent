import type { components } from "./api/openapi.gen";

export type TxStatus = components["schemas"]["TxStatus"];

export type Stage = "request" | "calling" | "negotiating" | "decision" | "confirming" | "done";
export type StageMark = "reached" | "current" | "skipped" | "future";
export type DoneOutcome = "confirmed" | "closed" | "failed";

export interface StageView {
  stage: Stage;
  label: string;
  mark: StageMark;
  outcome?: DoneOutcome;
}

export const STAGES: readonly { stage: Stage; label: string }[] = [
  { stage: "request", label: "Request" },
  { stage: "calling", label: "Calling" },
  { stage: "negotiating", label: "Negotiating" },
  { stage: "decision", label: "Your decision" },
  { stage: "confirming", label: "Confirming" },
  { stage: "done", label: "Done" },
];

// DESIGN.md decision 2. A Record keyed by the generated TxStatus union, so a status added in
// app/states.py fails `tsc` here until it's given a stage. APPROVED stays under "Your decision"
// while /approve starts no confirmation call (gap 2): lighting Confirming would claim a call that
// isn't happening.
export const STAGE_OF: Record<TxStatus, Stage> = {
  CREATED: "request",
  PROVIDER_SELECTED: "calling",
  CALLING: "calling",
  UNAVAILABLE: "calling",
  NEXT_PROVIDER: "calling",
  NEGOTIATING: "negotiating",
  AGREED_WITHIN_POLICY: "negotiating",
  OUTSIDE_AUTHORITY: "negotiating",
  RESULT_READY: "decision",
  AWAITING_APPROVAL: "decision",
  APPROVED: "decision",
  CONFIRMING: "confirming",
  CONFIRM_RETRY_WAIT: "confirming",
  CONFIRMATION_FAILED: "confirming",
  CONFIRMED: "done",
  DECLINED: "done",
  CLOSED: "done",
  FAILED: "done",
};

const DONE_OUTCOME: Partial<Record<TxStatus, DoneOutcome>> = {
  CONFIRMED: "confirmed",
  DECLINED: "closed",
  CLOSED: "closed",
  FAILED: "failed",
};

/**
 * Mark each timeline stage from the server's uncapped status_history, never from chip position:
 *
 *   index <  current   reached if some status in the history maps to it, otherwise skipped
 *   index == current   current (the Done stage also carries confirmed / closed / failed)
 *   index >  current   future, even if visited before the flow moved back a stage
 *
 * So a declined deal shows Confirming as skipped, not "Confirming ✓".
 */
export function reachedStages(status: TxStatus, history: readonly TxStatus[]): StageView[] {
  const current = STAGE_OF[status];
  const currentIndex = STAGES.findIndex((s) => s.stage === current);
  const visited = new Set<Stage>([...history, status].map((s) => STAGE_OF[s]));

  return STAGES.map(({ stage, label }, index): StageView => {
    if (index === currentIndex) {
      const outcome = stage === "done" ? DONE_OUTCOME[status] : undefined;
      return outcome ? { stage, label, mark: "current", outcome } : { stage, label, mark: "current" };
    }
    if (index > currentIndex) return { stage, label, mark: "future" };
    return { stage, label, mark: visited.has(stage) ? "reached" : "skipped" };
  });
}
