import { useEffect } from "react";

import type { TransactionView } from "./api/client";
import { AuditLog } from "./components/AuditLog";
import { MockStrip } from "./components/MockStrip";
import { StatusTimeline } from "./components/StatusTimeline";
import { FIXTURES } from "./fixtures";
import { parseSource } from "./source";
import { usePolledTransaction } from "./usePolledTransaction";

/**
 * The data-source seam (eng review D5). The URL picks a source once; components below
 * TransactionScreen only ever receive a TransactionView and never know where it came from.
 *
 *   ?mock=<fixture>  MockPage   fixtures only; the live poller is never constructed
 *   ?tx=<id>         LivePage   usePolledTransaction → the only fetch, in src/api/client.ts
 *   neither          EmptyState
 */
export function App({ search = window.location.search }: { search?: string }) {
  const source = parseSource(search);
  switch (source.kind) {
    case "mock":
      return <MockPage fixture={source.fixture} />;
    case "live":
      return <LivePage txId={source.txId} />;
    case "empty":
      return <EmptyState />;
  }
}

function MockPage({ fixture }: { fixture: string }) {
  const view = FIXTURES[fixture];
  return (
    <>
      <MockStrip />
      {view ? (
        <TransactionScreen view={view} />
      ) : (
        <main className="notice">
          <p className="notice-error">Unknown fixture: {fixture || "(none)"}</p>
          <ul className="fixture-links">
            {Object.keys(FIXTURES).map((name) => (
              <li key={name}>
                <a href={`?mock=${name}`}>{name}</a>
              </li>
            ))}
          </ul>
        </main>
      )}
    </>
  );
}

function LivePage({ txId }: { txId: string }) {
  const { status, view, detail } = usePolledTransaction(txId);

  useEffect(() => {
    if (status !== "not_found") return;
    const url = new URL(window.location.href);
    url.searchParams.delete("tx");
    window.history.replaceState(null, "", url);
  }, [status]);

  if (status === "not_found") {
    return <EmptyState notice="Transaction not found. The link may be wrong, or the database was reset." />;
  }
  if (status === "fatal") {
    return (
      <main className="notice">
        <p className="notice-error" role="alert">
          {detail}
        </p>
      </main>
    );
  }
  if (!view) {
    return (
      <main className="notice">
        <p role="status">{loadingMessage(status, detail)}</p>
      </main>
    );
  }
  return (
    <>
      {status === "reconnecting" && (
        <p className="banner" role="status">
          Reconnecting… showing the last update
        </p>
      )}
      {status === "server_error" && (
        <p className="banner banner-error" role="alert">
          Server error: {detail} (still retrying)
        </p>
      )}
      <TransactionScreen view={view} />
    </>
  );
}

function loadingMessage(status: string, detail: string | null): string {
  if (status === "server_error") return `Server error: ${detail} (still retrying)`;
  if (status === "reconnecting") return `Reconnecting… ${detail ?? ""}`.trim();
  return "Loading transaction…";
}

function TransactionScreen({ view }: { view: TransactionView }) {
  return (
    <>
      <StatusTimeline
        status={view.status}
        statusHistory={view.status_history}
        lastUpdateAt={view.audit[0]?.at ?? null}
      />
      {/* Build steps 2-3 add ConstraintCard, CallPanel and RecommendationCard here (DESIGN.md). */}
      <AuditLog events={view.audit} />
    </>
  );
}

function EmptyState({ notice }: { notice?: string }) {
  return (
    <main className="notice">
      {notice && (
        <p className="notice-error" role="alert">
          {notice}
        </p>
      )}
      <p>
        No transaction open. Open <code>?tx=&lt;id&gt;</code>, or preview with{" "}
        <a href="?mock=awaiting-approval">?mock=awaiting-approval</a>.
      </p>
    </main>
  );
}
