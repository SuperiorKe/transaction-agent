# Owner UI design (issue #7)

Resolved design decisions for `web/`, the single-page owner UI that judges watch for the whole
two-minute demo (doc 04 §10). Written before any code exists — `web/` is not yet scaffolded.

Ground truth is **issue #7** on `SuperiorKe/transaction-agent`; this file records the decisions that
issue left open, and the backend gaps found while resolving them. Where this file and the issue
disagree, the deviations are called out explicitly below.

> Location note: this lives at the repo root rather than `web/DESIGN.md` only because `web/` doesn't
> exist yet and scaffolding into an empty directory is cleaner. Move it into `web/` once the
> scaffold lands. Moving it also matters for tooling: gstack's design skills read a root
> `DESIGN.md` as the project's visual design system (fonts, colour and spacing tokens), which this
> file only partly is.

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

The confirmation half of the state machine is **unreachable over HTTP today**:

1. `app/orchestrator.py::retry_confirmation()` exists and is unit-tested, and
   `retry_confirmation` is a valid `AllowedAction` advertised by `ALLOWED_ACTIONS_BY_STATUS` for
   `CONFIRMATION_FAILED` and `CONFIRM_RETRY_WAIT` — but **no HTTP route exposes it**. Against the
   real API the button can't appear (gap 2 means neither status is reachable), but a mock fixture
   will render a button that 404s if clicked.
2. `app/orchestrator.py::start_confirmation()` is never called from `POST /{id}/approve` (there's an
   explicit comment in the route saying so), so `APPROVED` never advances to `CONFIRMING`.
   `APPROVED` is therefore where the demo ends after an approval: not terminal, no allowed actions.
3. Nothing calls `schedule_confirmation_retry()`, so `CONFIRM_RETRY_WAIT` is never *entered*, let
   alone left. Even when a `scheduler` is passed, the callback it receives is `lambda: None`.
4. When the daily call guard trips mid-fallback, `advance_from_unavailable()` leaves the transaction
   at `UNAVAILABLE` "for a human to retry", but `UNAVAILABLE` advertises no action, so no human can.

Tracked in `TODOS.md` → "Expose the confirmation half of the state machine over HTTP" (gaps 1–3)
and "Recover a transaction stuck at UNAVAILABLE after the call guard" (gap 4). Until gaps 1–3 land,
`ConfirmationPanel` is only exercisable through mock fixtures.

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
nodes are not legible at 1280×720. One `STAGE_OF: Record<string, Stage>` map:

| Stage chip    | `TxStatus` values                                                          |
| ------------- | -------------------------------------------------------------------------- |
| Request       | `CREATED`                                                                   |
| Calling       | `PROVIDER_SELECTED`, `CALLING`, `UNAVAILABLE`, `NEXT_PROVIDER`              |
| Negotiating   | `NEGOTIATING`, `AGREED_WITHIN_POLICY`, `OUTSIDE_AUTHORITY`                  |
| Your decision | `RESULT_READY`, `AWAITING_APPROVAL`, `APPROVED` (done: "Approved")          |
| Confirming    | `CONFIRMING`, `CONFIRM_RETRY_WAIT`, `CONFIRMATION_FAILED`                   |
| Done          | `CONFIRMED` (ok) · `DECLINED`/`CLOSED` (closed) · `FAILED` (failed)         |

The current chip is highlighted and the **raw status is printed underneath it in small text**, so a
judge sees the shape and a developer sees the precise state. Anything unmapped renders a visible
"unknown status" rather than a blank chip — a new status in `app/states.py` should be obvious, not
invisible.

`APPROVED` sits under "Your decision", not "Confirming", while gap 2 stands. Lighting the Confirming
chip would tell a judge a confirmation call is under way when none is, which is the on-screen form
of claiming confirmation before it exists. Move it back when `/approve` starts the confirmation
call.

When confirmation is wired, a provider changing terms on the confirmation call sends
`CONFIRMING → AGREED_WITHIN_POLICY/OUTSIDE_AUTHORITY`, so the highlight moves *back* a stage. That's
correct (the user must re-approve), not a rendering bug.

### 3. No `CONFIRM_RETRY_WAIT` countdown

*Deviation from issue #7*, which asks for one. `TransactionView` exposes no `retry_at` timestamp,
and nothing enters `CONFIRM_RETRY_WAIT` in production anyway (gap 3 above). Its allowed action is
already `retry_confirmation`, a manual action. Render the button, not a timer. No new API surface
for a cosmetic countdown.

### 4. Keep `mock.ts`, for a new reason, and label it on screen

Issue #7 wanted fixtures because the backend didn't exist. Issue #6 has since closed, so it does.
Keep them anyway: they are the only way to render `FAILED`, `CONFIRMATION_FAILED`,
`CONFIRM_RETRY_WAIT` and `CONFIRMED` without either filling the gaps above or placing repeated real
calls to the seeded providers.

`?mock=` must **fully short-circuit the network layer** — no polling, no fetches — so that
developing the UI can never accidentally fire `/start` against live Twilio credentials.

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
App
  ├─ ?mock=S1     → render fixture, no network at all, mock strip visible
  ├─ ?tx=<id>     → fetch once, then poll
  └─ neither      → RequestComposer only

poll: GET /transactions/{id} every 1000 ms
  stop when status ∈ {CONFIRMED, CLOSED, FAILED}   (= app/states.py::TERMINAL_STATES)
  on fetch error: keep the last good view, show a small "reconnecting" marker, keep polling
  on 404: clear ?tx= and fall back to the composer
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
  `font-variant-numeric: tabular-nums` and are always written `KES 23,000`. The raw status under
  the timeline is the one small-text element.
- **Colour:** neutral surfaces and one accent for the primary action. `WITHIN_LIMIT` and
  `REQUIRES_APPROVAL` each get a colour (green, amber) **and** their text label, never colour alone.
- **Over-cap approval:** when `policy_status` is `REQUIRES_APPROVAL` and there's a price, the Approve
  label carries the overage, `Approve KES 23,000 · KES 3,000 over your cap`. The canonical scenario
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

| Token          | Value                            | Used for                                                            |
| -------------- | -------------------------------- | ------------------------------------------------------------------- |
| `--bg`         | `#0d1117`                        | page ground                                                         |
| `--panel`      | `#151b23`                        | cards, stage chips                                                  |
| `--panel-2`    | `#1b2330`                        | quiet pills ("Call ended")                                          |
| `--line`       | `#2a3441`                        | borders, dividers                                                   |
| `--text`       | `#e6edf3`                        | primary text                                                        |
| `--muted`      | `#8d9aa8`                        | labels, secondary text, the provider's speaker label                |
| `--accent`     | `#2dd4e6`                        | the one primary action, the agent's speaker label, the brand dot     |
| `--accent-ink` | `#03171b`                        | text on `--accent`                                                  |
| `--amber`      | `#f2b441`, ground `rgba(242,180,65,.13)` | `REQUIRES_APPROVAL`, the current stage, amounts in the transcript |
| `--green`      | `#4ac26b`                        | done-stage ticks, `WITHIN_LIMIT`                                    |

**Type:** IBM Plex Sans (400–700) everywhere. IBM Plex Mono for speaker labels, the raw status,
the masked phone number and the call stats. Base 18px / 1.4. Final price 44px bold, provider
name 24px bold. The raw status line is 13px mono, still the one small-text element.

**Shape and spacing:** cards have a 1px `--line` border, 10px radius and 14px 18px padding. Stage
chips use a 6px radius, buttons 8px, pills are fully rounded. Page padding is 14px 24px, with 16px
between columns and 12px between cards.

**Components:**

- **StatusTimeline:** six equal chips beside the brand. Done chips show `--text` with a green ✓.
  The current chip has an amber border, an amber ground and a ●. Future chips are `--muted`. The
  raw status (in `--amber`) and "last update …" sit in mono under the current chip.
- **ConstraintCard:** a 2×2 grid of label over value (Service, When, Where, Your cap). The client's
  own word ("Monday") follows the resolved date in `--muted`.
- **RecommendationCard:** fills the rest of the left column. The header reads "Recommendation · ask
  you" beside a pill with an icon and text ("▲ Requires approval"), never colour alone. Below it: the
  price, provider · availability, terms and reason in `--muted`, then the actions pinned to the
  bottom. Approve is filled `--accent` with a two-line label (amount, then overage). Decline is a
  132px outlined button. While `policy_status` is `REQUIRES_APPROVAL`, the card gets a faint
  amber edge (`#4a3d22` border).
- **CallPanel:** provider name and masked phone on the left. Duration and Counteroffers stats sit
  on the right in mono, with a "Call ended" pill. Transcript rows use a 96px uppercase mono speaker
  column (agent in `--accent`, provider in `--muted`). Amounts spoken on the call are `--amber`
  semibold, so a judge can follow the numbers without reading every word.
- **AuditLog:** collapsed to one row under a top border.

**Check at rehearsal:** a dark ground can wash out on a projector in a bright room. If it does,
swap the token values for a light ground and keep their roles. Don't change the layout. The
filled Approve is the primary button even over the cap. That was accepted knowingly, and the
overage in its label is the counterweight.

## Component contracts

| Component             | Gets                                                               | Owns                          | Calls                                        |
| --------------------- | ------------------------------------------------------------------ | ----------------------------- | -------------------------------------------- |
| `RequestComposer`     | —                                                                  | `text`, `parsing`, `error`    | `POST /parse-request`                        |
| `ConstraintCard`      | `ParsedRequest`, composer `text`                                   | form fields, validity         | `POST /transactions` → `POST /{id}/start`    |
| `StatusTimeline`      | `tx.status`, `tx.audit[0].at`                                      | —                             | —                                            |
| `CallPanel`           | latest `kind="negotiation"` entry, `tx.current_provider`, `tx.max_attempts` | ticking timer off `answered_at` | —                                  |
| `RecommendationCard`  | `tx.recommendation`, `tx.allowed_actions`, `tx.max_budget`, offers | —                             | `POST /{id}/approve` · `/decline`            |
| `ConfirmationPanel`   | `tx.status`, `tx.allowed_actions`                                  | —                             | `POST /{id}/retry_confirmation` ⚠ route missing |
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
- FastAPI serves `web/dist/index.html` at `/` and `web/dist/assets` at `/assets`, but only when
  `web/dist/index.html` exists (`app/main.py`) — so the API runs fine with no UI built. The check
  runs once, when the app is created: after the first `npm run build`, restart uvicorn (`--reload`
  only watches Python files), or `/` stays 404.
- Owner routes are local-only: `app/middleware.py` 403s anything arriving through the cloudflared
  tunnel (it carries `cf-connecting-ip`) except `/webhooks/*`. The UI is therefore a localhost tool,
  not something to expose publicly.
- No component library. One CSS file. Single page.

## Build order

1. Scaffold + vite proxy + poll loop + `StatusTimeline` + `AuditLog` — smallest thing that proves
   the pipe works against an existing transaction.
2. `RequestComposer` + `ConstraintCard` — makes it usable without curl.
3. `CallPanel` + `RecommendationCard` — the parts judges actually watch. Lay them out against the
   1280×720 screen above from the start, not as a polish pass.
4. `mock.ts` covering the nine states in acceptance criterion 2, plus the mock strip.
5. *Backend work, separately:* the `retry_confirmation` route + calling `start_confirmation()` from
   `/approve` (see `TODOS.md`), then `ConfirmationPanel` last, and move `APPROVED` back under the
   Confirming stage.
