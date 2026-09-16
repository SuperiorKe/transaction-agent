import type { TransactionView } from "./api/client";
import type { TxStatus } from "./stages";

// Fixture data for ?mock= pages. It follows what the backend actually does: the photography
// scenario from CLAUDE.md, KES, masked phone numbers in the +2541 test range.
// Every fixture is built by transaction(), which derives `terminal` and the status.changed audit
// trail from `status_history`, so a fixture can't contradict itself. src/fixtures.test.ts checks.

type AuditEvent = TransactionView["audit"][number];
type Negotiation = TransactionView["negotiations"][number];
type Recommendation = TransactionView["recommendation"];

const TERMINAL: ReadonlySet<TxStatus> = new Set<TxStatus>(["CONFIRMED", "CLOSED", "FAILED"]);
const STARTED_AT = Date.parse("2026-09-14T08:40:00Z");
const REQUEST =
  "Photographer for Monday in Nairobi. Max KES 20,000. Negotiate twice; never agree above KES 20,000 without my approval.";
const STUDIO_A = { id: 1, name: "Studio A", phone_masked: "+254 1•• ••• 678" };

function at(secondsAfterStart: number): string {
  return new Date(STARTED_AT + secondsAfterStart * 1000).toISOString();
}

interface TransactionOptions {
  id: string;
  history: [TxStatus, ...TxStatus[]];
  negotiations?: Negotiation[];
  recommendation?: Recommendation;
  allowedActions?: TransactionView["allowed_actions"];
  extraAudit?: AuditEvent[];
}

function transaction({
  id,
  history,
  negotiations = [],
  recommendation = null,
  allowedActions = [],
  extraAudit = [],
}: TransactionOptions): TransactionView {
  const status = history[history.length - 1] ?? history[0];
  const changes: AuditEvent[] = history.slice(1).map((to, index) => ({
    type: "status.changed",
    payload: { from: history[index] ?? "CREATED", to, reason: "" },
    at: at(30 * (index + 1)),
  }));
  const created: AuditEvent = { type: "transaction.created", payload: { request: REQUEST }, at: at(0) };
  const audit = [created, ...changes, ...extraAudit].sort((a, b) => Date.parse(b.at) - Date.parse(a.at));

  return {
    id,
    status,
    terminal: TERMINAL.has(status),
    status_history: history,
    request: REQUEST,
    service: "photography",
    service_date: "2026-09-14",
    location: "Nairobi",
    max_budget: 20000,
    currency: "KES",
    max_attempts: 2,
    current_provider: history.includes("PROVIDER_SELECTED") ? STUDIO_A : null,
    negotiations,
    recommendation,
    allowed_actions: allowedActions,
    audit,
  };
}

const TO_NEGOTIATING: TxStatus[] = ["PROVIDER_SELECTED", "CALLING", "NEGOTIATING"];
const TO_APPROVAL: TxStatus[] = [...TO_NEGOTIATING, "OUTSIDE_AUTHORITY", "AWAITING_APPROVAL"];

const OVER_CAP_CALL: Negotiation = {
  id: "neg-1",
  kind: "negotiation",
  provider_name: "Studio A",
  status: "OUTSIDE_AUTHORITY",
  attempt_count: 1,
  dial_attempt: 1,
  answered_at: at(95),
  duration_seconds: 102,
  transcript: [
    {
      speaker: "agent",
      text: "Hi, I'm an AI assistant calling for a client. Are you free to photograph an event this Monday, 14 September, in Nairobi?",
      at: at(96),
    },
    { speaker: "provider", text: "Yes, Monday works. A full day is 23,000 shillings.", at: at(110) },
    { speaker: "agent", text: "My client's budget is 20,000. Could you do the day for 20,000?", at: at(120) },
    {
      speaker: "provider",
      text: "23,000 is my lowest. That covers 8 hours with edited photos, and there's no deposit.",
      at: at(140),
    },
    {
      speaker: "agent",
      text: "Understood. I can't agree above my client's limit, so I'll take the 23,000 offer back to them and call you back.",
      at: at(160),
    },
  ],
  offers: [
    {
      id: 11,
      amount: 23000,
      available: true,
      coverage_hours: 8,
      deposit_required: false,
      terms: "8 hours, edited photos, no deposit",
      decision: "MUST_ESCALATE",
      at: at(141),
    },
  ],
};

const OVER_CAP_RECOMMENDATION: Recommendation = {
  offer_id: 11,
  provider_name: "Studio A",
  final_price: 23000,
  policy_status: "REQUIRES_APPROVAL",
  recommendation: "ASK_USER",
  reason: "Final offer KES 23,000 is KES 3,000 (15%) over your KES 20,000 budget after 1 counteroffer(s).",
};

const OVER_CAP_EVENTS: AuditEvent[] = [
  { type: "counteroffer.made", payload: { amount: 20000 }, at: at(121) },
  { type: "offer.recorded", payload: { amount: 23000, available: true, coverage_hours: 8 }, at: at(141) },
];

export const FIXTURES: Record<string, TransactionView> = {
  created: transaction({ id: "mock-created", history: ["CREATED"], allowedActions: ["start"] }),

  negotiating: transaction({
    id: "mock-negotiating",
    history: ["CREATED", ...TO_NEGOTIATING],
    negotiations: [
      {
        ...OVER_CAP_CALL,
        status: "IN_PROGRESS",
        attempt_count: 0,
        duration_seconds: null,
        transcript: OVER_CAP_CALL.transcript.slice(0, 2),
        offers: [],
      },
    ],
  }),

  "awaiting-approval": transaction({
    id: "mock-awaiting-approval",
    history: ["CREATED", ...TO_APPROVAL],
    negotiations: [OVER_CAP_CALL],
    recommendation: OVER_CAP_RECOMMENDATION,
    allowedActions: ["approve", "decline"],
    extraAudit: OVER_CAP_EVENTS,
  }),

  declined: transaction({
    id: "mock-declined",
    history: ["CREATED", ...TO_APPROVAL, "DECLINED", "CLOSED"],
    negotiations: [OVER_CAP_CALL],
    recommendation: OVER_CAP_RECOMMENDATION,
    extraAudit: OVER_CAP_EVENTS,
  }),

  failed: transaction({
    id: "mock-failed",
    history: ["CREATED", "PROVIDER_SELECTED", "CALLING", "UNAVAILABLE", "FAILED"],
    negotiations: [
      {
        ...OVER_CAP_CALL,
        status: "NO_ANSWER",
        attempt_count: 0,
        answered_at: null,
        duration_seconds: null,
        transcript: [],
        offers: [],
      },
    ],
    recommendation: {
      offer_id: null,
      provider_name: null,
      final_price: null,
      policy_status: "NONE",
      recommendation: "DECLINE",
      reason: "No provider available on Mon 14 Sep: Studio A: no answer.",
    },
  }),
};
