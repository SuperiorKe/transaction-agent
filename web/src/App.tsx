import { useCallback, useEffect, useRef, useState } from "react";

import {
  type ParsedRequest,
  type TransactionCreate,
  type TransactionView,
  approveTransaction,
  createTransaction,
  declineTransaction,
  parseRequest,
  retryConfirmation,
  startTransaction,
} from "./api/client";
import { AuditLog } from "./components/AuditLog";
import { CallPanel } from "./components/CallPanel";
import { MockStrip } from "./components/MockStrip";
import { RecommendationCard } from "./components/RecommendationCard";
import { StatusTimeline } from "./components/StatusTimeline";
import { FIXTURES } from "./fixtures";
import { formatKes } from "./format";
import { parseSource } from "./source";
import type { PollStatus } from "./usePolledTransaction";
import { usePolledTransaction } from "./usePolledTransaction";

interface NewTransaction {
  id: string;
  dateText: string | null;
}

/** The URL chooses mock/live mode; a newly-created transaction moves to live mode immediately. */
export function App({ search = window.location.search }: { search?: string }) {
  const [newTransaction, setNewTransaction] = useState<NewTransaction | null>(null);
  const source = newTransaction ? { kind: "live" as const, txId: newTransaction.id } : parseSource(search);

  const openCreated = useCallback((id: string, dateText: string | null) => {
    const url = new URL(window.location.href);
    url.searchParams.set("tx", id);
    window.history.pushState(null, "", url);
    setNewTransaction({ id, dateText });
  }, []);

  switch (source.kind) {
    case "mock":
      return <MockPage fixture={source.fixture} />;
    case "live":
      return (
        <LivePage
          txId={source.txId}
          startImmediately={newTransaction?.id === source.txId}
          dateText={newTransaction?.id === source.txId ? newTransaction.dateText : null}
          onCreated={openCreated}
        />
      );
    case "empty":
      return <EmptyState onCreated={openCreated} />;
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

function LivePage({
  txId,
  startImmediately,
  dateText,
  onCreated,
}: {
  txId: string;
  startImmediately: boolean;
  dateText: string | null;
  onCreated: (id: string, dateText: string | null) => void;
}) {
  const { status, view, detail } = usePolledTransaction(txId);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [decisionPending, setDecisionPending] = useState(false);
  const [decisionError, setDecisionError] = useState<string | null>(null);
  const initialStartDone = useRef(false);

  const start = useCallback(async () => {
    setStarting(true);
    setStartError(null);
    const result = await startTransaction(txId);
    if (!result.ok) setStartError(result.detail);
    setStarting(false);
  }, [txId]);

  const decide = useCallback(async (request: () => Promise<{ ok: boolean; detail?: string }>) => {
    setDecisionPending(true);
    setDecisionError(null);
    const result = await request();
    if (!result.ok) setDecisionError(result.detail ?? "The request failed");
    // Deliberately do not put result.value into local state. The poll is the source of truth for
    // confirmation progress and for callbacks that can arrive while this request is settling.
    setDecisionPending(false);
  }, []);

  const approve = useCallback(
    (offerId: number) => decide(() => approveTransaction(txId, offerId)),
    [decide, txId],
  );
  const decline = useCallback(() => decide(() => declineTransaction(txId)), [decide, txId]);
  const retry = useCallback(() => decide(() => retryConfirmation(txId)), [decide, txId]);

  useEffect(() => {
    if (!startImmediately || initialStartDone.current) return;
    initialStartDone.current = true;
    void start();
  }, [start, startImmediately]);

  useEffect(() => {
    if (status !== "not_found") return;
    const url = new URL(window.location.href);
    url.searchParams.delete("tx");
    window.history.replaceState(null, "", url);
  }, [status]);

  if (status === "not_found") {
    return (
      <EmptyState
        notice="Transaction not found. The link may be wrong, or the database was reset."
        onCreated={onCreated}
      />
    );
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
          {serverErrorText(detail)}
        </p>
      )}
      <TransactionScreen
        view={view}
        dateText={dateText}
        onStart={view.allowed_actions.includes("start") ? start : undefined}
        starting={starting}
        startError={startError}
        decisionPending={decisionPending}
        decisionError={decisionError}
        onApprove={approve}
        onDecline={decline}
        onRetry={retry}
      />
    </>
  );
}

function serverErrorText(detail: string | null): string {
  return `Server error: ${detail ?? "unknown"} (still retrying)`;
}

function loadingMessage(status: PollStatus, detail: string | null): string {
  if (status === "server_error") return serverErrorText(detail);
  if (status === "reconnecting") return `Reconnecting… ${detail ?? ""}`.trim();
  return "Loading transaction…";
}

function TransactionScreen({
  view,
  dateText = null,
  onStart,
  starting = false,
  startError = null,
  decisionPending = false,
  decisionError = null,
  onApprove,
  onDecline,
  onRetry,
}: {
  view: TransactionView;
  dateText?: string | null;
  onStart?: () => Promise<void>;
  starting?: boolean;
  startError?: string | null;
  decisionPending?: boolean;
  decisionError?: string | null;
  onApprove?: (offerId: number) => Promise<void>;
  onDecline?: () => Promise<void>;
  onRetry?: () => Promise<void>;
}) {
  return (
    <>
      <StatusTimeline
        status={view.status}
        statusHistory={view.status_history}
        lastUpdateAt={view.audit[0]?.at ?? null}
      />
      <section className="transaction-layout" aria-label="Transaction details">
        <ConstraintCard view={view} dateText={dateText} onStart={onStart} starting={starting} error={startError} />
        <CallPanel view={view} />
        <RecommendationCard
          view={view}
          pending={decisionPending}
          error={decisionError}
          onApprove={onApprove}
          onDecline={onDecline}
          onRetry={onRetry}
        />
      </section>
      <AuditLog events={view.audit} />
    </>
  );
}

function EmptyState({
  notice,
  onCreated,
}: {
  notice?: string;
  onCreated?: (id: string, dateText: string | null) => void;
}) {
  const [text, setText] = useState("");
  const [parsed, setParsed] = useState<ParsedRequest | null>(null);
  const [parsing, setParsing] = useState(false);
  const [parseError, setParseError] = useState<string | null>(null);

  const readRequest = async () => {
    setParsing(true);
    setParseError(null);
    const result = await parseRequest(text);
    if (result.ok) setParsed(result.value);
    else setParseError(result.detail);
    setParsing(false);
  };

  return (
    <main className="composer-page">
      {notice && (
        <p className="notice-error" role="alert">
          {notice}
        </p>
      )}
      <section className="card request-composer" aria-labelledby="request-heading">
        <h1 id="request-heading">New request</h1>
        <label htmlFor="request-text">Client request</label>
        <textarea
          id="request-text"
          value={text}
          onChange={(event) => setText(event.target.value)}
          maxLength={500}
          rows={6}
          placeholder="Photographer for Monday in Nairobi. Max KES 20,000."
        />
        <button type="button" onClick={() => void readRequest()} disabled={!text.trim() || parsing}>
          {parsing ? "Reading request…" : "Read request"}
        </button>
        {parseError && (
          <p className="form-error" role="alert">
            {parseError}
          </p>
        )}
      </section>
      {parsed && onCreated && <EditableConstraintCard parsed={parsed} request={text} onCreated={onCreated} />}
    </main>
  );
}

function EditableConstraintCard({
  parsed,
  request,
  onCreated,
}: {
  parsed: ParsedRequest;
  request: string;
  onCreated: (id: string, dateText: string | null) => void;
}) {
  const [serviceDate, setServiceDate] = useState(parsed.service_date ?? "");
  const [location, setLocation] = useState(parsed.location ?? "");
  const [budget, setBudget] = useState(parsed.max_budget?.toString() ?? "");
  const [attempts, setAttempts] = useState<"0" | "1" | "2">(String(parsed.max_attempts) as "0" | "1" | "2");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dateValid = isAllowedNairobiDate(serviceDate);
  const budgetNumber = Number(budget);
  const budgetValid = /^\d+$/.test(budget) && budgetNumber >= 1000 && budgetNumber <= 1_000_000;
  const locationValid = location.trim().length > 0;
  const supported = parsed.service === "photography";
  const canStart = supported && dateValid && locationValid && budgetValid && !creating;

  const createAndStart = async () => {
    if (!canStart) return;
    setCreating(true);
    setError(null);
    const body: TransactionCreate = {
      request,
      service: "photography",
      service_date: serviceDate,
      location: location.trim(),
      max_budget: budgetNumber,
      max_attempts: Number(attempts) as 0 | 1 | 2,
    };
    const result = await createTransaction(body);
    if (result.ok) onCreated(result.value.id, parsed.date_text ?? null);
    else {
      setError(result.detail);
      setCreating(false);
    }
  };

  return (
    <section className="card constraint-card" aria-labelledby="constraints-heading">
      <h2 id="constraints-heading">Constraints</h2>
      <div className="constraint-grid">
        <label>
          Service
          <output className={supported ? "" : "unsupported"}>{parsed.service}</output>
        </label>
        <label>
          When
          <input type="date" value={serviceDate} min={nairobiToday()} max={nairobiMaxDate()} onChange={(event) => setServiceDate(event.target.value)} />
          {serviceDate && <span className="field-hint">{formatNairobiDate(serviceDate)}</span>}
          {parsed.date_text && <span className="field-hint">Client said “{parsed.date_text}”</span>}
        </label>
        <label>
          Where
          <input value={location} maxLength={200} onChange={(event) => setLocation(event.target.value)} />
        </label>
        <label>
          Your cap (KES)
          <input inputMode="numeric" value={budget} onChange={(event) => setBudget(event.target.value)} />
        </label>
        <label>
          Counteroffers
          <select value={attempts} onChange={(event) => setAttempts(event.target.value as "0" | "1" | "2")}>
            <option value="0">0</option>
            <option value="1">1</option>
            <option value="2">2</option>
          </select>
        </label>
      </div>
      {!supported && <p className="form-error" role="alert">✕ Only photography is supported in this prototype.</p>}
      {error && <p className="form-error" role="alert">{error}</p>}
      <button type="button" className="start-button" disabled={!canStart} onClick={() => void createAndStart()}>
        {creating ? "Creating transaction…" : "Start call"}
      </button>
    </section>
  );
}

function ConstraintCard({
  view,
  dateText,
  onStart,
  starting,
  error,
}: {
  view: TransactionView;
  dateText: string | null;
  onStart?: () => Promise<void>;
  starting: boolean;
  error: string | null;
}) {
  return (
    <section className="card constraint-card" aria-labelledby="constraints-heading">
      <h2 id="constraints-heading">Constraints</h2>
      <dl className="constraint-grid constraint-readonly">
        <div><dt>Service</dt><dd>{view.service}</dd></div>
        <div><dt>When</dt><dd>{formatNairobiDate(view.service_date)} {dateText && <span className="field-hint">(“{dateText}”)</span>}</dd></div>
        <div><dt>Where</dt><dd>{view.location}</dd></div>
        <div><dt>Your cap</dt><dd>KES {formatKes(view.max_budget)}</dd></div>
        <div><dt>Counteroffers</dt><dd>{view.max_attempts}</dd></div>
      </dl>
      {error && <p className="form-error" role="alert">{error}</p>}
      {onStart && (
        <button type="button" className="start-button" disabled={starting} onClick={() => void onStart()}>
          {starting ? "Starting call…" : "Start call"}
        </button>
      )}
    </section>
  );
}

function nairobiToday(): string {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Nairobi", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts();
  const part = (type: string) => parts.find((item) => item.type === type)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")}`;
}

function nairobiMaxDate(): string {
  const date = new Date(`${nairobiToday()}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + 90);
  return date.toISOString().slice(0, 10);
}

function isAllowedNairobiDate(value: string): boolean {
  return /^\d{4}-\d{2}-\d{2}$/.test(value) && value >= nairobiToday() && value <= nairobiMaxDate();
}

function formatNairobiDate(value: string): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  return new Intl.DateTimeFormat("en-GB", { timeZone: "Africa/Nairobi", weekday: "long", day: "numeric", month: "short", year: "numeric" }).format(new Date(`${value}T12:00:00+03:00`));
}
