# Owner UI design (issue #7)

Resolved design decisions for `web/`, the single-page owner UI that judges watch for the whole
two-minute demo (doc 04 §10). Written before any code exists — `web/` is not yet scaffolded.

Ground truth is **issue #7** on `SuperiorKe/transaction-agent`; this file records the decisions that
issue left open, and the two backend gaps found while resolving them. Where this file and the issue
disagree, the deviations are called out explicitly below.

> Location note: this lives at the repo root rather than `web/DESIGN.md` only because `web/` doesn't
> exist yet and scaffolding into an empty directory is cleaner. Move it into `web/` once the
> scaffold lands.

## The one non-negotiable rule

**The frontend displays authority; it never computes it.**

Every action button's existence comes from `TransactionView.allowed_actions`, never from a status
comparison in React. The server already computes this in
`app/routes/transactions.py::ALLOWED_ACTIONS_BY_STATUS`. A frontend that checks
`status === "RESULT_READY"` to decide whether to show Approve has re-implemented the state machine
on the client, where it can drift from `app/states.py`. This is the UI-layer version of the
project's central thesis: the LLM does language, deterministic code enforces authority — and the
browser is just another untrusted edge.

## Known backend gaps (do not design around these silently)

The confirmation half of the state machine is **unreachable over HTTP today**:

1. `app/orchestrator.py::retry_confirmation()` exists and is unit-tested, and
   `retry_confirmation` is a valid `AllowedAction` advertised by `ALLOWED_ACTIONS_BY_STATUS` for
   `CONFIRMATION_FAILED` and `CONFIRM_RETRY_WAIT` — but **no HTTP route exposes it**. An
   `allowed_actions`-driven UI will faithfully render a button that 404s.
2. `app/orchestrator.py::start_confirmation()` is never called from `POST /{id}/approve` (there's an
   explicit comment in the route saying so), so `APPROVED` never advances to `CONFIRMING`.
3. `schedule_confirmation_retry()`'s `scheduler` argument defaults to a no-op, so nothing leaves
   `CONFIRM_RETRY_WAIT` on its own in production.

Tracked in `TODOS.md` → "Expose the confirmation half of the state machine over HTTP". Until it
lands, `ConfirmationPanel` is only exercisable through mock fixtures.

## Resolved decisions

### 1. Create and start are one user action, two API calls

`TransactionCreate` requires `service_date`, `location` and `max_budget` to all be present, while
`ParsedRequest` permits nulls plus a `missing[]` list. So a transaction *cannot* be created straight
after parsing if anything is missing. The flow is therefore:

```
"Read request"  → POST /parse-request        → populate the constraint form (blanks where missing)
                  (nothing exists server-side yet — no orphan CREATED rows if abandoned)
"Start call"    → POST /transactions         → got an id, write it to ?tx=
                → POST /transactions/{id}/start
```

If the second call fails (429 from the call guard, network drop), the transaction still exists in
`CREATED` with `allowed_actions: ["start"]`. The UI just renders the transaction view, sees `start`
is allowed, and shows the button again. **The retry path falls out of server-truth rendering for
free** — no special-case error state.

### 2. `StatusTimeline` collapses 17 statuses into 6 stages

*Deviation from issue #7*, which says "the contract 2 states, current one highlighted". Seventeen
nodes are not legible at 1280×720. One `STAGE_OF: Record<string, Stage>` map:

| Stage chip    | `TxStatus` values                                                          |
| ------------- | -------------------------------------------------------------------------- |
| Request       | `CREATED`                                                                   |
| Calling       | `PROVIDER_SELECTED`, `CALLING`, `UNAVAILABLE`, `NEXT_PROVIDER`              |
| Negotiating   | `NEGOTIATING`, `AGREED_WITHIN_POLICY`, `OUTSIDE_AUTHORITY`                  |
| Your decision | `RESULT_READY`, `AWAITING_APPROVAL`                                         |
| Confirming    | `APPROVED`, `CONFIRMING`, `CONFIRM_RETRY_WAIT`, `CONFIRMATION_FAILED`       |
| Done          | `CONFIRMED` (ok) · `DECLINED`/`CLOSED` (closed) · `FAILED` (failed)         |

The current chip is highlighted and the **raw status is printed underneath it in small text**, so a
judge sees the shape and a developer sees the precise state. Anything unmapped renders a visible
"unknown status" rather than a blank chip — a new status in `app/states.py` should be obvious, not
invisible.

### 3. No `CONFIRM_RETRY_WAIT` countdown

*Deviation from issue #7*, which asks for one. `TransactionView` exposes no `retry_at` timestamp,
and nothing schedules the retry in production anyway (gap 3 above). `CONFIRM_RETRY_WAIT`'s allowed
action is already `retry_confirmation`, a manual action. Render the button, not a timer. No new API
surface for a cosmetic countdown.

### 4. Keep `mock.ts`, for a new reason

Issue #7 wanted fixtures because the backend didn't exist. Issue #6 has since closed, so it does.
Keep them anyway: they are the only way to render `FAILED`, `CONFIRMATION_FAILED`,
`CONFIRM_RETRY_WAIT` and `CONFIRMED` without either filling the gaps above or placing repeated real
calls to the seeded providers.

`?mock=` must **fully short-circuit the network layer** — no polling, no fetches — so that
developing the UI can never accidentally fire `/start` against live Twilio credentials.

## Page state model

```
App
  ├─ ?mock=S1     → render fixture, no network at all
  ├─ ?tx=<id>     → fetch once, then poll
  └─ neither      → RequestComposer only

poll: GET /transactions/{id} every 1000 ms
  stop when status ∈ {CONFIRMED, CLOSED, FAILED}   (= app/states.py::TERMINAL_STATES)
  on fetch error: keep the last good view, show a small "reconnecting" marker, keep polling
  on 404: clear ?tx= and fall back to the composer
```

Polling over SSE/WebSocket: acceptance criterion 4 only requires a transcript turn to appear within
2 s, which 1 s polling satisfies with margin, and it needs zero new backend surface.

The transaction id lives in `?tx=` so a refresh mid-demo restores the view. Constraint-form state
before creation is in-memory only — there is no server row to restore yet, by design (decision 1).

## Component contracts

| Component             | Gets                                             | Owns                          | Calls                                        |
| --------------------- | ------------------------------------------------ | ----------------------------- | -------------------------------------------- |
| `RequestComposer`     | —                                                | `text`, `parsing`, `error`    | `POST /parse-request`                        |
| `ConstraintCard`      | `ParsedRequest`                                  | form fields, validity         | `POST /transactions` → `POST /{id}/start`    |
| `StatusTimeline`      | `tx.status`                                      | —                             | —                                            |
| `CallPanel`           | latest `tx.negotiations[]`, `tx.max_attempts`    | ticking timer off `answered_at` | —                                          |
| `RecommendationCard`  | `tx.recommendation`, `tx.allowed_actions`        | —                             | `POST /{id}/approve` · `/decline`            |
| `ConfirmationPanel`   | `tx.status`, `tx.allowed_actions`                | —                             | `POST /{id}/retry_confirmation` ⚠ route missing |
| `AuditLog`            | `tx.audit[]`                                     | collapsed/expanded            | —                                            |

Notes:

- **`ConstraintCard`** — service is fixed to `photography`; budget is whole KES (`ge=1000`,
  `le=1_000_000`); attempts is `0 | 1 | 2`; `service_date` must fall in `[today, today+90]` in
  `Africa/Nairobi` (the route enforces this and returns 422). Start stays disabled while
  `parsed.missing` is non-empty or local validation fails.
- **`CallPanel`** — `tx.negotiations` is ordered by `created_at` ascending, so the active one is the
  last element; a transaction accumulates several across provider fallback and redials. The live
  timer ticks only while `duration_seconds` is null; once set, freeze it. Counteroffers render as
  `attempt_count` / `tx.max_attempts`. Provider phone is already masked server-side
  (`phone_masked`) — never render an unmasked number.
- **`RecommendationCard`** — fields in doc 03 §7 order: provider, availability, final price, terms,
  policy status, recommendation, reason. Note that `RecommendationView` only carries
  `provider_name`, `final_price`, `policy_status`, `recommendation`, `reason` and `offer_id` —
  **availability and terms are not on it**, and must be joined client-side from the matching
  `OfferView` in `tx.negotiations[].offers[]` by `recommendation.offer_id` (which also supplies
  `coverage_hours`). Approve sends `{offer_id: recommendation.offer_id}` and is labelled with the
  amount (`Approve KES 21,000`); a stale `offer_id` returns 409.
- **`AuditLog`** — `tx.audit` is newest-first, capped at 50 server-side.

## Wiring

- `vite.config.ts` proxies `/parse-request`, `/transactions` and `/health` to
  `http://localhost:8000`.
- FastAPI serves `web/dist/index.html` at `/` and `web/dist/assets` at `/assets`, but only when
  `web/dist/index.html` exists (`app/main.py`) — so the API runs fine with no UI built.
- Owner routes are local-only: `app/middleware.py` 403s anything arriving through the cloudflared
  tunnel (it carries `cf-connecting-ip`) except `/webhooks/*`. The UI is therefore a localhost tool,
  not something to expose publicly.
- No component library. One CSS file. Single page.

## Build order

1. Scaffold + vite proxy + poll loop + `StatusTimeline` + `AuditLog` — smallest thing that proves
   the pipe works against an existing transaction.
2. `RequestComposer` + `ConstraintCard` — makes it usable without curl.
3. `CallPanel` + `RecommendationCard` — the parts judges actually watch.
4. `mock.ts` covering the nine states in acceptance criterion 2.
5. *Backend work, separately:* the `retry_confirmation` route + calling `start_confirmation()` from
   `/approve` (see `TODOS.md`), then `ConfirmationPanel` last.
