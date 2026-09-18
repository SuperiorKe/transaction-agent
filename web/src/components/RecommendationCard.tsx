import type { TransactionView } from "../api/client";
import { formatKes } from "../format";

type Mutation = (() => Promise<void>) | undefined;

// "Recommendation · accept" / "· ask you" / "· decline" (DESIGN.md, RecommendationCard header).
const HEADER_SUFFIX: Record<NonNullable<TransactionView["recommendation"]>["recommendation"], string> = {
  ACCEPT: "accept",
  ASK_USER: "ask you",
  DECLINE: "decline",
};

export function RecommendationCard({
  view,
  pending = false,
  error = null,
  onApprove,
  onDecline,
  onRetry,
}: {
  view: TransactionView;
  pending?: boolean;
  error?: string | null;
  onApprove?: (offerId: number) => Promise<void>;
  onDecline?: Mutation;
  onRetry?: Mutation;
}) {
  const recommendation = view.recommendation;
  const offer = recommendation?.offer_id === null || recommendation === null
    ? undefined
    : view.negotiations.flatMap((negotiation) => negotiation.offers).find((item) => item.id === recommendation.offer_id);
  const showApprove = view.allowed_actions.includes("approve") && recommendation?.offer_id !== null && recommendation !== null;
  const showDecline = view.allowed_actions.includes("decline");
  const showRetry = view.allowed_actions.includes("retry_confirmation");
  // "The approval styling (the pill and an --amber-edge border) shows only while approve or
  // decline is in tx.allowed_actions; the recommendation row survives into APPROVED and CLOSED,
  // where 'Requires approval' would be stale" (DESIGN.md).
  const decisionPending = showApprove || showDecline;
  const showAmberEdge =
    decisionPending && recommendation !== null && recommendation.policy_status === "REQUIRES_APPROVAL";

  return (
    <section
      className={`card recommendation-card${showAmberEdge ? " recommendation-card-pending-approval" : ""}`}
      aria-labelledby="recommendation-heading"
    >
      <h2 id="recommendation-heading">
        Recommendation{recommendation && ` · ${HEADER_SUFFIX[recommendation.recommendation]}`}
      </h2>
      {view.terminal ? (
        <p className="recommendation-state terminal-state">{terminalMessage(view.status)}</p>
      ) : recommendation === null ? (
        <p className="recommendation-state">No recommendation yet.</p>
      ) : offer === undefined && recommendation.offer_id !== null ? (
        <p className="recommendation-state unavailable-state">This recommendation refers to an offer that is no longer available.</p>
      ) : (
        <RecommendationDetails
          recommendation={recommendation}
          offer={offer}
          showPill={decisionPending}
        />
      )}
      {(showApprove || showDecline || showRetry) && (
        <div className="decision-actions">
          {showApprove && (
            <button
              type="button"
              className="approve-button"
              disabled={pending || !onApprove}
              onClick={() => recommendation && recommendation.offer_id !== null && void onApprove?.(recommendation.offer_id)}
            >
              {pending ? "Updating…" : <ApproveLabel recommendation={recommendation} maxBudget={view.max_budget} />}
            </button>
          )}
          {showDecline && (
            <button type="button" disabled={pending || !onDecline} onClick={() => void onDecline?.()}>
              {pending ? "Updating…" : "Decline"}
            </button>
          )}
          {showRetry && (
            <button type="button" disabled={pending || !onRetry} onClick={() => void onRetry?.()}>
              {pending ? "Retrying…" : "Retry confirmation"}
            </button>
          )}
        </div>
      )}
      {error && <p className="form-error" role="alert">{error}</p>}
    </section>
  );
}

/**
 * "Approve KES 21,000", plus a second label line with the overage when `final_price` is above
 * `tx.max_budget` (DESIGN.md: "Approve KES 23,000 · KES 3,000 over your cap"). Only rendered when
 * showApprove already established there's a priced, matched offer, so final_price is never null
 * here.
 */
function ApproveLabel({
  recommendation,
  maxBudget,
}: {
  recommendation: NonNullable<TransactionView["recommendation"]>;
  maxBudget: number;
}) {
  const finalPrice = recommendation.final_price ?? 0;
  const overage = finalPrice - maxBudget;
  return (
    <>
      Approve KES {formatKes(finalPrice)}
      {overage > 0 && (
        <>
          {" "}
          <span className="approve-overage">· KES {formatKes(overage)} over your cap</span>
        </>
      )}
    </>
  );
}

function RecommendationDetails({
  recommendation,
  offer,
  showPill,
}: {
  recommendation: NonNullable<TransactionView["recommendation"]>;
  offer: TransactionView["negotiations"][number]["offers"][number] | undefined;
  showPill: boolean;
}) {
  if (recommendation.offer_id === null || recommendation.final_price === null || !offer || offer.amount === null) {
    return <p className="recommendation-state unavailable-state">No priced offer is available.</p>;
  }
  if (!offer.available) {
    return <p className="recommendation-state unavailable-state">This provider is unavailable for the requested service.</p>;
  }
  return (
    <>
      {showPill && recommendation.policy_status !== "NONE" && <PolicyPill status={recommendation.policy_status} />}
      <dl className="recommendation-facts">
        <div><dt>Provider</dt><dd>{recommendation.provider_name ?? "Unknown provider"}</dd></div>
        <div><dt>Price</dt><dd className="recommendation-price">KES {formatKes(recommendation.final_price)}</dd></div>
        <div><dt>Coverage</dt><dd>{offer.coverage_hours === null ? "Not specified" : `${offer.coverage_hours} hours`}</dd></div>
        <div><dt>Deposit</dt><dd>{offer.deposit_required === null ? "Not specified" : offer.deposit_required ? "Required" : "None"}</dd></div>
        {offer.terms && <div className="recommendation-terms"><dt>Terms</dt><dd>{offer.terms}</dd></div>}
      </dl>
      <p className="recommendation-reason">{recommendation.reason}</p>
    </>
  );
}

// WITHIN_LIMIT is a --green "check Within limit"; REQUIRES_APPROVAL is --amber on --amber-ground
// "triangle Requires approval" (DESIGN.md). Inline SVG, not ▲/✓ text: same reasoning as
// StatusTimeline's StageIcon -- IBM Plex doesn't reliably render those glyphs.
function PolicyPill({ status }: { status: "WITHIN_LIMIT" | "REQUIRES_APPROVAL" }) {
  const within = status === "WITHIN_LIMIT";
  return (
    <span className={`policy-pill ${within ? "policy-pill-within" : "policy-pill-approval"}`}>
      <PolicyIcon icon={within ? "check" : "triangle"} />
      {within ? "Within limit" : "Requires approval"}
    </span>
  );
}

function PolicyIcon({ icon }: { icon: "check" | "triangle" }) {
  return (
    <svg
      className="policy-icon"
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
      {icon === "triangle" && <path d="M8 3l6 10.5H2z" fill="currentColor" />}
    </svg>
  );
}

function terminalMessage(status: string): string {
  if (status === "CONFIRMED") return "Booking confirmed.";
  if (status === "CLOSED") return "You declined this offer.";
  if (status === "FAILED") return "No provider could be booked.";
  return "This transaction is complete.";
}
