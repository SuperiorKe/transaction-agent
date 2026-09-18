import type { TransactionView } from "../api/client";
import { formatKes } from "../format";

type Mutation = (() => Promise<void>) | undefined;

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

  return (
    <section className="card recommendation-card" aria-labelledby="recommendation-heading">
      <h2 id="recommendation-heading">Recommendation</h2>
      {view.terminal ? (
        <p className="recommendation-state terminal-state">{terminalMessage(view.status)}</p>
      ) : recommendation === null ? (
        <p className="recommendation-state">No recommendation yet.</p>
      ) : offer === undefined && recommendation.offer_id !== null ? (
        <p className="recommendation-state unavailable-state">This recommendation refers to an offer that is no longer available.</p>
      ) : (
        <RecommendationDetails recommendation={recommendation} offer={offer} />
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
              {pending ? "Updating…" : "Approve"}
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

function RecommendationDetails({
  recommendation,
  offer,
}: {
  recommendation: NonNullable<TransactionView["recommendation"]>;
  offer: TransactionView["negotiations"][number]["offers"][number] | undefined;
}) {
  if (recommendation.offer_id === null || recommendation.final_price === null || !offer || offer.amount === null) {
    return <p className="recommendation-state unavailable-state">No priced offer is available.</p>;
  }
  if (!offer.available) {
    return <p className="recommendation-state unavailable-state">This provider is unavailable for the requested service.</p>;
  }
  return (
    <>
      <p className={`recommendation-state ${recommendation.policy_status === "REQUIRES_APPROVAL" ? "approval-state" : "within-limit-state"}`}>
        {recommendation.policy_status === "WITHIN_LIMIT" ? "Within your limit" : "Requires your approval"}
      </p>
      <dl className="recommendation-facts">
        <div><dt>Provider</dt><dd>{recommendation.provider_name ?? "Unknown provider"}</dd></div>
        <div><dt>Price</dt><dd>KES {formatKes(recommendation.final_price)}</dd></div>
        <div><dt>Coverage</dt><dd>{offer.coverage_hours === null ? "Not specified" : `${offer.coverage_hours} hours`}</dd></div>
        <div><dt>Deposit</dt><dd>{offer.deposit_required === null ? "Not specified" : offer.deposit_required ? "Required" : "None"}</dd></div>
        {offer.terms && <div className="recommendation-terms"><dt>Terms</dt><dd>{offer.terms}</dd></div>}
      </dl>
      <p className="recommendation-reason">{recommendation.reason}</p>
    </>
  );
}

function terminalMessage(status: string): string {
  if (status === "CONFIRMED") return "Booking confirmed.";
  if (status === "CLOSED") return "You declined this offer.";
  if (status === "FAILED") return "No provider could be booked.";
  return "This transaction is complete.";
}
