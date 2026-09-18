# Owner UI design (issue #7)

Resolved design decisions for `web/`, the single-page owner UI that judges watch for the whole
two-minute demo (doc 04 §10). All five build steps below have landed: the scaffold, poll loop,
`StatusTimeline` and `AuditLog` (step 1); the request composer (step 2); `CallPanel` and
`RecommendationCard` (step 3); the fixture set (step 4); and the confirmation-call backend wiring
(step 5).

Ground truth is **issue #7** on `SuperiorKe/transaction-agent`; this file records the decisions that
issue left open, and the backend gaps found while resolving them. Where this file and the issue
disagree, the deviations are called out explicitly below.

> Location note: this stays at the repo root. It started here because `web/` didn't exist yet, and
> it now also holds the visual system (fonts, colour and spacing tokens), which gstack's design
> skills read from a root `DESIGN.md`. Don't move it into `web/`.

## The one non-negotiable rule

**The frontend displays authority; it never computes it.**

Every action button's existence comes from `TransactionView.allowed_actions`, never from a status
comparison in React. The server computes this in `app/routes/transactions.py::_build_view`: the
status picks candidates from `ALLOWED_ACTIONS_BY_STATUS`, and `approve` is then dropped when the
current recommendation has no `offer_id` (nothing approvable exists). A frontend that checks
`status === "RESULT_READY"` to decide whether to show Approve has re-implemented the state machine
on the client, where it can drift from `app/states.py`. This is the UI-layer version of the
project's central thesis: the LLM does language, deterministic code enforces authority — and the
browser is just another untrusted edge.

Corollary: when the server advertises an action it cannot serve, fix the server. Don't paper over
it with a client-side condition.

## Known backend gaps (do not design around these silently)

Gaps 1–3 below (the confirmation half of the state machine unreachable over HTTP) are **fixed**:
`POST /{id}/approve` calls `start_confirmation()` and `POST /{id}/retry-confirmation` calls
`retry_confirmation()` on a fresh `Negotiation` row per retry, guard-checked like every other dial.
`CONFIRM_RETRY_WAIT` was removed from the state machine entirely rather than wired up — nothing
ever entered it, and `schedule_confirmation_retry()`, its only would-be producer, is gone.

What's still open:

1. `CONFIRMING` advances only on a Twilio callback, with no timeout and no `allowed_actions` entry:
   if the callback never arrives (the tunnel rotates, cloudflared dies), the transaction polls
   forever with nothing the owner can press.
2. `CONFIRMATION_FAILED` only leads back to `CONFIRMING` via retry — there's no `decline`/abandon
   action if the owner wants to stop trying, unlike `RESULT_READY`/`AWAITING_APPROVAL`.
3. `POST /{id}/approve` starts confirmation in the same request as recording the approval, but
   catches only `StaleOffer`/`CallGuardBlocked`. Any other exception between the two commits (an
   unset `current_provider_id`, a DB error) leaves the transaction at `APPROVED`, which has one
   outgoing edge and no advertised action.
4. When the daily call guard trips mid-fallback, `advance_from_unavailable()` leaves the transaction
   at `UNAVAILABLE` "for a human to retry", but `UNAVAILABLE` advertises no action, so no human can.

Tracked in `TODOS.md` → "Recover from CONFIRMING / CONFIRMATION_FAILED with no owner-facing way
out" (gaps 1 and 3) and "Recover a transaction stuck at UNAVAILABLE after the call guard" (gap 4).
Gap 2 (no abandon action from `CONFIRMATION_FAILED`) is the same recovery-action design question
as gap 1 and is tracked alongside it.

Fixed alongside this design, so no longer gaps:

- A 429 from `POST /{id}/start` used to strand the transaction at `PROVIDER_SELECTED` with no
  allowed actions (`select_provider` committed before `place_call` checked the guard). The route
  now checks the guard first, so a 429 leaves it at `CREATED` with `["start"]`.
- `approve` used to be advertised for an escalation with no quoted price (`offer_id: null`).

## Resolved decisions

### 1. Create and start are one user action, two API calls

`TransactionCreate` requires `service_date`, `location` and `max_budget` to all be present, while
`ParsedRequest` permits nulls plus a `missing[]` list. So a transaction *cannot* be created straight
after parsing if anything is missing. The flow is therefore:

```
"Read request"  → POST /parse-request        → populate the constraint form (blanks where missing)
                  (nothing exists server-side yet — no orphan CREATED rows if abandoned)
"Start call"    → POST /transactions         → got an id, write it to ?tx=, start polling
                → POST /transactions/{id}/start
```

`TransactionCreate.request` is the composer's original text; `ParsedRequest` doesn't echo it, so
it's carried from `RequestComposer` into `ConstraintCard`.

If the second call fails (429 from the call guard, network drop), the transaction still exists in
`CREATED` with `allowed_actions: ["start"]`. The UI just renders the transaction view, sees `start`
is allowed, and shows the button again. **The retry path falls out of server-truth rendering for
free** — no special-case error state. (This depends on the route checking the call guard before
selecting a provider; `tests/test_api.py` pins it.)

`POST /start` awaits the dial before responding, so polling starts as soon as `?tx=` is written
(the timeline shows `CALLING` while the request is still open), and Start is disabled while the
request is in flight — a second POST would 409.

### 2. `StatusTimeline` collapses 18 statuses into 6 stages

*Deviation from issue #7*, which says "the contract 2 states, current one highlighted". Eighteen
nodes are not legible at 1280×720. The mapping is `STAGE_OF: Record<TxStatus, Stage>` in
`web/src/stages.ts`. It's keyed by the enum `/openapi.json` publishes, so a new status fails `tsc`
until it's mapped:

| Stage chip    | `TxStatus` values                                                          |
| ------------- | -------------------------------------------------------------------------- |
| Request       | `CREATED`                                                                   |
| Calling       | `PROVIDER_SELECTED`, `CALLING`, `UNAVAILABLE`, `NEXT_PROVIDER`              |
| Negotiating   | `NEGOTIATING`, `AGREED_WITHIN_POLICY`, `OUTSIDE_AUTHORITY`                  |
| Your decision | `RESULT_READY`, `AWAITING_APPROVAL`                                        |
| Confirming    | `APPROVED`, `CONFIRMING`, `CONFIRMATION_FAILED`                            |
| Done          | `CONFIRMED` (ok) · `DECLINED`/`CLOSED` (closed) · `FAILED` (failed)         |

The current chip is highlighted and the **raw status is printed under the chip row in small text**,
so a judge sees the shape and a developer sees the precise state. An unmapped status can't reach
the screen, so there's no "unknown status" chip:
- `tsc` rejects an incomplete map.
- A value outside the enum fails the API's response validation. That's a 500, and the poll loop
  reports three in a row as a server error.

`APPROVED` sits under "Confirming", not "Your decision": approval immediately starts the
confirmation call (`POST /{id}/approve` now calls `start_confirmation()` in the same request), so
by the time the owner UI's next poll lands, the transaction is already past `APPROVED` in practice.
Lighting the Confirming chip for the brief interval it's actually at `APPROVED` is accurate, not
premature — unlike the pre-wiring design, there's no longer a real state where "Confirming" would
be shown before a call exists.

When confirmation is wired, a provider changing terms on the confirmation call sends
`CONFIRMING → AGREED_WITHIN_POLICY/OUTSIDE_AUTHORITY`, so the highlight moves *back* a stage. That's
correct (the user must re-approve), not a rendering bug.

### 3. No countdown for a confirmation retry

*Deviation from issue #7*, which asks for one. `TransactionView` exposes no `retry_at` timestamp.
The status issue #7 had in mind for this (`CONFIRM_RETRY_WAIT`, a timed auto-retry) was removed
from the state machine entirely rather than wired up: nothing ever produced it, and confirmation
retry is a manual owner action (`retry_confirmation`) from `CONFIRMATION_FAILED`, not a countdown.
Render the button, not a timer. No new API surface for a cosmetic countdown.

### 4. Keep fixtures (`web/src/fixtures.ts`), for a new reason, and label them on screen

Issue #7 wanted fixtures because the backend didn't exist. Issue #6 has since closed, so it does.
Keep them anyway: they are the only way to render `FAILED`, `CONFIRMATION_FAILED` and `CONFIRMED`
without placing repeated real calls to the seeded providers.

`?mock=` must **fully short-circuit the network layer** — no polling, no fetches — so that
developing the UI can never accidentally fire `/start` against live Twilio credentials. The
guarantee is enforced by the code structure:
- `App` picks its data source once from the URL.
- `?mock=` wins over `?tx=`. An unknown or empty fixture name is a mock-mode error, never a
  fallthrough to live.
- `web/src/boundary.test.ts` fails if `fetch(` appears anywhere except `web/src/api/client.ts`.

Fixtures are named (`?mock=awaiting-approval`) and follow the backend's photography scenario. Each
one is built from its `status_history`, so its audit trail can't contradict it.

Mock mode shows a fixed, full-width strip reading **"Mock data — not a live call"** that can't be
dismissed. A fixture's transcript and prices look exactly like a live call's; the project rule for
the rehearsal recording (labelled on screen, never presented as live) applies to fixtures too.

### 5. An unsupported service blocks Start; it is never coerced to photography

`ParsedRequest.service` is `"photography" | "unsupported"`, and `missing[]` never mentions it. A form
with the service "fixed to photography" would turn "find me a plumber for Monday" into a real call
to a photographer. So `ConstraintCard` renders `parsed.service`, and when it is `"unsupported"`
shows "Only photography is supported in this prototype" and keeps Start disabled. The backend
already refuses it (`TransactionCreate.service` is `Literal["photography"]`, 422), so this is
honest feedback, not enforcement.

### 6. A transaction that stops moving says so

Some non-terminal statuses can sit indefinitely with no allowed action: `APPROVED` (gap 2) and
`UNAVAILABLE` after a mid-fallback call-guard block (gap 4). A highlighted chip alone reads as "in
progress" forever. Under the timeline, print "last update 2 min ago" from the newest
`tx.audit[0].at`. It's a display of server data, not a client-side guess about the state.

## Page state model

```
App (web/src/App.tsx) picks a source once from the URL
  ├─ ?mock=<name>  → fixture, no network at all, mock strip visible (wins over ?tx=)
  ├─ ?tx=<id>      → poll
  └─ neither       → empty state (RequestComposer arrives in build step 2)

poll (web/src/usePolledTransaction.ts): GET /transactions/{id}, one request at a time; the next
is scheduled 1000 ms after the previous one settles
  ok                   replace the view; stop when view.terminal (the server's flag)
  unreachable          network error, 502/503/504, or no answer in 5 s: keep the last good
                       view, "reconnecting" banner, keep polling
  5xx / bad 200        same, until 3 in a row: "Server error: <detail>", still retrying
                       (a 200 that isn't shaped like a transaction counts as a bad 200)
  404                  stop, drop ?tx= from the URL, empty state saying "Transaction not found"
  other 4xx            stop and show the server's detail (a 422 list is joined into a string)
  unmount / new id     abort the in-flight request and ignore its result
```

Polling over SSE/WebSocket: acceptance criterion 4 only requires a transcript turn to appear within
2 s, which 1 s polling satisfies with margin, and it needs zero new backend surface. `APPROVED` is
not terminal, so polling continues after an approval; at 1 req/s against localhost that's accepted
rather than special-cased.

The transaction id lives in `?tx=` so a refresh mid-demo restores the view. Constraint-form state
before creation is in-memory only — there is no server row to restore yet, by design (decision 1).

## Screen at 1280×720

Acceptance criterion 5 is a projector: usable at 1280×720, no horizontal scroll. Proposed layout,
to be checked on the real projector at rehearsal:

```
┌────────────────────────────────────────────────────────────────────────────┐
│ [mock strip, only with ?mock=]                                             │
│ StatusTimeline: 6 chips · raw status · "last update …"                     │
├──────────────────────────────┬─────────────────────────────────────────────┤
│ ConstraintCard (read-only    │ CallPanel                                   │
│ once created)                │ Studio A · +254 7•• ••• 678 · 01:42         │
│ Photography · Mon 14 Sep     │ Counteroffers 1/2                           │
│ ("Monday") · Nairobi         │ ┌─────────────────────────────────────────┐ │
│ Cap KES 20,000 · 2 attempts  │ │ transcript, newest at the bottom,       │ │
├──────────────────────────────┤ │ scrolls inside this box                 │ │
│ RecommendationCard           │ │                                         │ │
│ KES 23,000 · Requires        │ └─────────────────────────────────────────┘ │
│ approval · reason            │                                             │
│ [Approve KES 23,000 ·        │                                             │
│  KES 3,000 over your cap]    │                                             │
│ [Decline]                    │                                             │
└──────────────────────────────┴─────────────────────────────────────────────┘
  AuditLog (collapsed) — the only thing allowed below the fold
```

- **Columns:** about 2:3, left for what was asked and what came back, right for the live call. Before
  a transaction exists, `RequestComposer` + `ConstraintCard` sit in one centred column.
- **Type:** 18px base, because judges read it from across a room. Amounts use
  `font-variant-numeric: tabular-nums` and are always written `KES 23,000`. One line may be small:
  the status line under the timeline (raw status · last update).
- **Colour:** neutral surfaces and one accent, used only for the primary action. `WITHIN_LIMIT`,
  `REQUIRES_APPROVAL` and failure each get a colour (green, amber, red) **and** an icon and text
  label, never colour alone.
- **Over-cap approval:** when `final_price` is above `tx.max_budget`, the Approve label carries the
  overage, `Approve KES 23,000 · KES 3,000 over your cap`. Key it off the two numbers, not
  `policy_status`: escalations that aren't about price (a deposit request, say) are also
  `REQUIRES_APPROVAL`, and there the overage would read "KES 0 over your cap". The canonical scenario
  ends exactly here, and the user is granting authority beyond their own cap. The overage is
  `final_price − tx.max_budget`, shown, not used to decide anything.
- **No horizontal scroll:** the transcript wraps, and no element has a fixed width wider than its
  column.

## Visual system

Approved on 14 Sep 2026 with `/design-shotgun`. Three directions were rendered as real 1280×720
HTML pages of the canonical approval moment: this dark "Control Room" look, a warm-paper serif
report, and a white decision-first hero with a cap-vs-quote bar. The mockup and its source HTML
live in gstack's design store (`designs/approval-moment-20260913/variant-A.*`), not in this repo.
Everything needed to build it is below.

**Tokens** (CSS custom properties on `:root`):

| Token            | Value                  | Used for                                                                     |
| ---------------- | ---------------------- | ---------------------------------------------------------------------------- |
| `--bg`           | `#0d1117`              | page ground                                                                  |
| `--panel`        | `#151b23`              | cards, stage chips                                                           |
| `--panel-2`      | `#1b2330`              | quiet pills ("Call ended")                                                   |
| `--line`         | `#2a3441`              | borders, dividers                                                            |
| `--text`         | `#e6edf3`              | primary text, the agent's speaker label, the recommendation's reason         |
| `--muted`        | `#8d9aa8`              | labels, terms and availability, the provider's speaker label, the brand dot  |
| `--accent`       | `#2dd4e6`              | the one primary action (Approve), and nothing else                           |
| `--accent-ink`   | `#03171b`              | text on `--accent`                                                           |
| `--amber`        | `#f2b441`              | `REQUIRES_APPROVAL`, the current stage                                       |
| `--amber-ground` | `rgba(242,180,65,.13)` | ground of the current chip and the approval pill                             |
| `--amber-edge`   | `#4a3d22`              | RecommendationCard border while an approval is pending                       |
| `--green`        | `#4ac26b`              | reached-stage ticks, `CONFIRMED`, `WITHIN_LIMIT`                             |
| `--red`          | `#f47067`              | `FAILED` chips and server errors, always with ✕ or a text label              |

**Type:** IBM Plex Sans (400–700) everywhere, with IBM Plex Mono for speaker labels, the raw status,
the masked phone number and the call stats. Bundle both with the app (for example
`@fontsource/ibm-plex-sans` and `@fontsource/ibm-plex-mono`) instead of loading Google Fonts: on
venue Wi-Fi a late font swap reflows a screen fitted to 720px. Check that ▲ ✓ ● ✕ render in Plex,
and use inline SVG icons if they fall back. Base 18px / 1.4, and everything is 18px except: the
final price (44px bold), the provider name (24px bold), and the status line under the timeline
(13px mono, the one small-text element).

**Shape and spacing:** cards have a 1px `--line` border, 10px radius and 14px 18px padding. Stage
chips use a 6px radius, buttons 8px, pills are fully rounded. Page padding is 14px 24px, with 16px
between columns and 12px between cards.

**Components:**

- **StatusTimeline:** six equal chips beside the brand (the product name after a `--muted` dot,
  owned by this component). A chip gets a green ✓ only for a stage the transaction actually
  reached, never from the chip's position. Reached stages come from `tx.status_history`, the
  server's ordered and uncapped list; the 50-event audit list is too short for a real call.
  `DECLINED` and `CLOSED` skip Confirming, and `FAILED` can follow straight from Request or
  Calling, so a skipped stage stays `--muted` with no mark. The current chip has an `--amber`
  border, an `--amber-ground` fill and a ●, except in the terminal Done stage: `CONFIRMED` gets a
  green ✓, `DECLINED`/`CLOSED` a `--muted` "Closed", and `FAILED` a `--red` ✕ "Failed". Stages
  after the current one are `--muted` "upcoming", even if the flow visited them before moving back. The status line (raw status
  in the current chip's colour · "last update …", 13px mono) runs full width under the whole chip
  row, because a status like `AGREED_WITHIN_POLICY` doesn't fit under one chip.
- **ConstraintCard:** once the transaction exists, a read-only grid of label over value: Service,
  When, Where, Your cap, and Counteroffers (up to `tx.max_attempts`). The client's own word
  ("Monday") follows the resolved date in `--muted`. `date_text` isn't on `TransactionView`, so
  after a `?tx=` refresh show the resolved date alone; don't re-parse `tx.request`. Build the
  weekday from the ISO date's parts or format it with `timeZone: "Africa/Nairobi"`:
  `new Date("2026-09-14")` is UTC midnight, which shows Sunday in any browser west of UTC. Before
  creation the same card is the editable form, in the same tokens, and the unsupported-service
  message (decision 5) carries an icon and text, never colour alone.
- **RecommendationCard:** fills the rest of the left column. The header follows `recommendation`
  ("Recommendation · accept", "· ask you", "· decline") and the pill follows `policy_status`:
  `WITHIN_LIMIT` is a `--green` "✓ Within limit", `REQUIRES_APPROVAL` is `--amber` on
  `--amber-ground` "▲ Requires approval", and `NONE` shows no pill. Below it: the price, provider
  · availability and terms in `--muted`, and the reason in `--text`. A long reason (an over-budget
  explanation with a dropped-call note, or a list of provider outcomes) scrolls inside the card, so
  the actions pinned to the bottom never leave the 720px screen. Show the price large only when
  the joined offer is `available`: a `DECLINE` for an unavailable provider can still carry an
  `offer_id`, so show "Unavailable on <date>" there, and "No price quoted" when `final_price` is
  null. Approve is filled `--accent`, and its second label line (the overage) appears only when
  `final_price` is above `tx.max_budget`. Decline is a 132px outlined button. Disable both while
  either request is in flight. The approval styling (the pill and an `--amber-edge` border) shows
  only while `approve` or `decline` is in `tx.allowed_actions`; the recommendation row survives
  into `APPROVED` and `CLOSED`, where "Requires approval" would be stale.
- **CallPanel:** provider name and masked phone on the left. Duration and Counteroffers stats sit
  on the right in mono, beside a status pill derived from the negotiation's `status`: "Dialing"
  (`DIALING`), "Ringing" (`RINGING`), "Live" (`IN_PROGRESS`), and "Call ended" for every other
  `NegStatus`, so "Call ended" never shows before the call has ended. Transcript rows use a 96px
  uppercase mono speaker column: `agent` in `--text` bold, `provider` in `--muted`, and `system`
  turns in `--muted` italic with no label. Amounts are `--text` semibold, so a judge can follow
  the numbers, but only amounts that match one of this negotiation's `offers[].amount` (as `23,000`
  or `23000`); don't pull other numbers out of the speech text. Build highlighted turns as React
  nodes, never `dangerouslySetInnerHTML`: the text is the provider's speech and the model's words.
- **AuditLog:** collapsed to one row under a top border.

**Check at rehearsal:** a dark ground can wash out on a projector in a bright room. If it does,
move to a light ground and keep every token's role, but choose new values instead of reusing
these: on white, `--amber` is 1.85:1, `--green` 2.28:1 and `--accent` 1.80:1, all too faint for
text. Don't change the layout. The filled Approve is the primary button even over the cap. The
owner chose that over two variants that gave Approve less weight, and the overage in its label is
the counterweight.

## Component contracts

| Component             | Gets                                                               | Owns                          | Calls                                        |
| --------------------- | ------------------------------------------------------------------ | ----------------------------- | -------------------------------------------- |
| `RequestComposer`     | —                                                                  | `text`, `parsing`, `error`    | `POST /parse-request`                        |
| `ConstraintCard`      | `ParsedRequest`, composer `text`; once created, `tx.service`, `tx.service_date`, `tx.location`, `tx.max_budget`, `tx.max_attempts` | form fields, validity | `POST /transactions` → `POST /{id}/start` |
| `StatusTimeline`      | `tx.status`, `tx.status_history` (reached stages), `tx.audit[0].at` (last update) | — | —                              |
| `CallPanel`           | latest negotiation (either `kind`), `tx.current_provider`, `tx.max_attempts` | ticking timer off `answered_at` | —                                  |
| `RecommendationCard`  | `tx.recommendation`, `tx.allowed_actions`, `tx.max_budget`, offers | —                             | `POST /{id}/approve` · `/decline` · `/retry-confirmation` |
| `AuditLog`            | `tx.audit[]`                                                       | collapsed/expanded            | —                                            |

Notes:

- **`ConstraintCard`** — service comes from `parsed.service` and blocks on `"unsupported"`
  (decision 5); budget is whole KES (`ge=1000`, `le=1_000_000`); attempts is `0 | 1 | 2`;
  `service_date` must fall in `[today, today+90]` in `Africa/Nairobi` (the route enforces this and
  returns 422). The date input shows the weekday, and `parsed.date_text` ("Monday") beside it, so
  the owner can see the client's words were resolved to the right day. Start stays disabled while
  `parsed.missing` is non-empty, the service is unsupported, or local validation fails.
- **`CallPanel`** — `tx.negotiations` is ordered by `created_at` ascending, and holds both
  `kind="negotiation"` and `kind="confirmation"` calls, so the panel shows the last entry with
  `kind="negotiation"`; a transaction accumulates several across provider fallback and redials.
  The timer ticks only while `answered_at` is set, `duration_seconds` is null and the negotiation's
  `status` is `IN_PROGRESS`; otherwise freeze it (a missed end callback must not leave it running).
  Counteroffers render as `attempt_count` / `tx.max_attempts` (`attempt_count` is counteroffers
  spoken). The masked phone is `tx.current_provider.phone_masked` — `NegotiationView` only carries
  `provider_name`. Never render an unmasked number.
- **`RecommendationCard`** — fields in doc 03 §7 order: provider, availability, final price, terms,
  policy status, recommendation, reason. Note that `RecommendationView` only carries
  `provider_name`, `final_price`, `policy_status`, `recommendation`, `reason` and `offer_id` —
  **availability and terms are not on it**, and must be joined client-side from the matching
  `OfferView` in `tx.negotiations[].offers[]` by `recommendation.offer_id` (which also supplies
  `coverage_hours`). Approve sends `{offer_id: recommendation.offer_id}` and is labelled with the
  amount (`Approve KES 21,000`, plus the overage when over the cap); a stale `offer_id` returns 409.
  `offer_id` and `final_price` can be null (an escalation before any price, or a `DECLINE` after
  every provider failed): render "No price quoted" and the reason, and the server won't have
  advertised `approve`.
- **`AuditLog`** — `tx.audit` is newest-first, capped at 50 server-side.

## Wiring

- `vite.config.ts` proxies `/parse-request`, `/transactions` and `/health` to
  `http://localhost:8000`.
- FastAPI serves `web/dist/index.html` at `/` and `web/dist/assets` at `/assets`, checking on every
  request (`app/main.py`, `create_app(web_dist=...)`). A build made after uvicorn started is served
  with no restart. A missing build is a 404 saying to run `npm run build`, and the API runs fine
  without it. `index.html` is sent `Cache-Control: no-cache`, because a rebuild deletes the old
  hashed assets. It is also sent `frame-ancestors 'none'`, `X-Frame-Options: DENY` and `nosniff`,
  because the page will carry Start and Approve buttons.
- The TypeScript API types are generated, never hand-written. `web/openapi.json` is the committed
  contract (`uv run python -m app.openapi_export`, guarded by a stale-snapshot pytest). `npm test`,
  `npm run build` and `npm run dev` regenerate `web/src/api/openapi.gen.ts` from it; that file is
  gitignored.
- `npm run e2e` (Playwright 1.62.1, 1280×720) builds `web/dist-e2e` (never the `web/dist` a
  running demo serves), then runs `scripts/e2e_server.py`: a
  temp SQLite file, no `.env`, `FakeTelephonyProvider`, and a test-only route that calls
  `transition()`. It checks the served page follows real status changes within 2 s.
- Owner routes are local-only: `app/middleware.py` 403s anything arriving through the cloudflared
  tunnel (it carries `cf-connecting-ip`) except `/webhooks/*`. The UI is therefore a localhost tool,
  not something to expose publicly.
- No component library. One CSS file. Single page.

## Build order

1. Scaffold + vite proxy + poll loop + `StatusTimeline` + `AuditLog` — smallest thing that proves
   the pipe works against an existing transaction. **Done.** The eng review added the mock data
   source, five fixtures and the mock strip to this step, so no later step runs against the live
   API without a safety net.
2. `RequestComposer` + `ConstraintCard` — makes it usable without curl. **Done**, as the composer
   inside `App.tsx` rather than a standalone `RequestComposer` component.
3. `CallPanel` + `RecommendationCard` — the parts judges actually watch. Lay them out against the
   1280×720 screen above from the start, not as a polish pass. **Done.** No separate
   `ConfirmationPanel`: a confirmation call renders through `CallPanel` like any other call
   (`kind="confirmation"` shows a "Confirmation" badge), and retry is a `RecommendationCard` button
   gated on `allowed_actions`, per the component contract table above.
4. The remaining fixtures, so `web/src/fixtures.ts` covers all nine states in acceptance criterion
   2. Step 1 already covers `CREATED`, `NEGOTIATING`, `AWAITING_APPROVAL`, a declined `CLOSED` and
   `FAILED`, plus the mock strip. **Done** for the states the backend can actually reach —
   `web/src/fixtures.ts` has 11 fixtures now, including within-limit, confirmation-in-progress,
   confirmation-failed, confirmed and changed-terms. No fixture for `CALLING` (a fixture would
   need to represent a call that's still ringing, which the other Calling-stage fixtures don't).
5. *Backend work, separately:* the `retry_confirmation` route + calling `start_confirmation()` from
   `/approve` (see `TODOS.md`), and move `APPROVED` back under the Confirming stage. **Done.**
