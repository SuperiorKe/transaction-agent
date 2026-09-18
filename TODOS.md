# TODOS

The primary backlog is GitHub issues: epic #10 and sub-issues #1-#9. This file holds follow-ups that come out of reviews and ships. Format: `~/.claude/skills/gstack/review/TODOS-format.md`.

## Ship

### Enforce CALL_HARD_END_SECONDS

**What:** Nothing currently reads or enforces `Settings.call_hard_end_seconds` (default 180) anywhere in the call-handling path.

**Why:** Without a circuit breaker, a chatty/adversarial provider or a model that keeps asking clarifying questions can hold a real outbound call open indefinitely, accruing Africa's Talking per-minute and Anthropic API cost with no cap. `max_tool_rounds` only bounds a single turn, not the whole call.

**Context:** Found by the `/ship` adversarial review on `arch/africastalking-anthropic`. The config field and the `"time.hard_end"` audit event type already exist (epic #10 contract), so this looks intended rather than an oversight — confirm scope against issue #6 (Orchestrator and API) before the first live rehearsal.

**Effort:** S
**Priority:** P1
**Depends on:** Issue #6 (orchestrator/full state machine)

### Confirm at-internal.com is a real Africa's Talking recording host

**What:** `DEFAULT_RECORDING_HOSTS` in `app/telephony/africastalking.py` (and `Settings.at_recording_hosts`) includes `at-internal.com`, which the adapter's own docstring flags as unverified against a live number.

**Why:** This host is on the SSRF-defense allowlist that gates which hosts `_transcribe` will download `recordingUrl` from. If it's a placeholder rather than a domain AT actually controls, anyone who registers it (plus the webhook secret) could feed a fabricated transcript into a live negotiation.

**Context:** Found by the `/ship` adversarial review. Confirm against Africa's Talking's current voice docs during the first real-call rehearsal (issue #2), and drop it from the default allowlist if it doesn't check out.

**Effort:** S
**Priority:** P1
**Depends on:** Issue #2 (telephony proof)

### Commit the provider's words before awaiting the model

**What:** In `app/agent/session.py` `respond()`, commit the provider's transcript turn before `await self._run(...)`. That way a poll sees what the provider said while the agent's reply is still being generated.

**Why:** Today `_write_transcript("provider", message.text)` only reaches the database at the `self._session.commit()` after the model returns. So the owner UI's transcript lags a full model round behind the call. Acceptance criterion 4 of #7 (a turn visible within 2 s) would measure poll speed, not what judges see.

The outside-voice review also suspects that once `record_offer` flushes, a SQLite write lock is held across the rest of the model rounds. That could block `/approve` or Twilio status callbacks. This is unverified.

**Context:** Found by the outside-voice pass of `/plan-eng-review` on owner UI build step 1 (2026-09-14).
- The comment in `respond()` says the provider turn is written before the engine runs so `record_offer`'s amount-heard check sees it; committing keeps that working.
- Decide what a committed provider turn means if the model call then fails. Probably fine, since it's what was actually said.
- Test the lock theory with two sessions against a file-backed SQLite DB before and after the change.

**Effort:** S
**Priority:** P1
**Depends on:** None; must land before owner UI build step 3 (CallPanel)

### Recover from CONFIRMING / CONFIRMATION_FAILED with no owner-facing way out

**What:** `POST /{id}/approve` now calls `start_confirmation()` and `POST /{id}/retry-confirmation`
now calls `retry_confirmation()` — the HTTP wiring this item used to be about is done, along with
the unbounded-call and stale-negotiation-reuse bugs an adversarial `/ship` review found in the
retry path (retries now guard-check and dial a fresh `Negotiation` row instead of reusing the
failed one), and the confirmation call now runs on `CONFIRMATION_SYSTEM_PROMPT` instead of the
negotiation prompt. What's left: `CONFIRMING` advances only on a Twilio callback, with no timeout
and no `allowed_actions` entry — if the callback is lost (tunnel rotates, cloudflared dies), the
transaction polls forever with nothing the owner can press. `CONFIRMATION_FAILED` only ever leads
back to `CONFIRMING` via retry; there's no `decline`/abandon action if the owner wants to give up
on confirming after a failed attempt, unlike `RESULT_READY`/`AWAITING_APPROVAL`, which both offer
`decline`.

**Why:** A stuck `CONFIRMING` or a `CONFIRMATION_FAILED` an owner doesn't want to keep retrying are
both dead ends the owner UI can only render as "waiting" forever.

**Context:** Found by the `/ship` adversarial review of owner UI build steps 2-3 (2026-09-18).
Also unresolved from the same review: `POST /approve` starts confirmation in the same request as
recording the approval, with only `StaleOffer`/`CallGuardBlocked` caught — any other exception
between the two (an unset `current_provider_id`, a DB error) leaves the transaction at `APPROVED`,
which has one outgoing edge and no advertised action. Consider either catching more broadly and
rolling the status back, or giving `APPROVED` its own recovery action.

**Effort:** M
**Priority:** P1
**Depends on:** None

### Distinguish STT failures from caller silence in the audit trail

**What:** `AfricasTalkingVoiceProvider._transcribe` swallows any speech-to-text exception (auth, network, quota) and returns `""`, which `CallCoordinator` then treats as ordinary caller silence. `app/audit.py`'s `EVENT_TYPES` has no distinct type for it, and no new module in this diff calls `record_event` at all yet.

**Why:** A misconfigured `GOOGLE_APPLICATION_CREDENTIALS` during a live demo would look identical to the caller just not speaking — the agent repeats "could you say that again?" with nothing in the audit trail to tell the two apart.

**Context:** Found by the `/ship` adversarial review. Wiring `record_event` calls into the call-handling path is naturally part of issue #6 (persistence/orchestration); add a `speech.failed`-style event type to the closed set in `app/audit.py` at the same time.

**Effort:** M
**Priority:** P2
**Depends on:** Issue #6

### Keep Twilio's error code and message when a call request fails

**What:** In `app/telephony/twilio.py`, add Twilio's JSON `code` and `message` from a 4xx body to the `TelephonyError` text, redacting any E.164 number. Don't retry non-retryable 4xx responses in `place_call`.

**Why:** Today the adapter keeps only `f"Twilio call request failed: HTTP {exc.response.status_code}"`, and the orchestrator audits that string. CLAUDE.md warns about the likeliest demo-day failure: the trial account rejects an unverified destination number. When that happens, the owner UI's audit log shows `HTTP 400` twice, then the timeline lands on Failed, with nothing saying why.

**Context:** Found by the `/ship` red team on owner UI build step 1 (2026-09-16). This predates the owner UI; the audit log just makes the gap visible. Twilio 4xx bodies are JSON with `code`, `message` and `more_info`. Code 21219 is "unverified number" on trial accounts.

**Effort:** S
**Priority:** P2
**Depends on:** None

### Recover a transaction stuck at UNAVAILABLE after the call guard

**What:** When the daily call guard trips during provider fallback, `advance_from_unavailable` in
`app/orchestrator.py` returns and leaves the transaction at `UNAVAILABLE` "for a human to retry
once the daily limit resets". `ALLOWED_ACTIONS_BY_STATUS` advertises nothing for `UNAVAILABLE` and
no route resumes the cascade, so no human can.

**Why:** The transaction is non-terminal with no way forward: the owner UI polls it forever and can
only show "last update N min ago" (DESIGN.md decision 6).

**Context:** Found by `/design-review` of `DESIGN.md` (gap 4). Options: move it to `FAILED` with a
`DECLINE` recommendation that says the daily limit was hit (simplest, and honest), or add a
`resume` action + route that re-enters `advance_from_unavailable`.

**Effort:** S (fail it) / M (resume route)
**Priority:** P3
**Depends on:** None

### Webhook secret entropy + basic abuse throttling

**What:** `VOICE_WEBHOOK_SECRET` is the sole authentication for `/webhooks/voice/{secret}` (AT doesn't sign requests). Nothing validates its length/entropy at startup, and there's no rate limiting or backoff on repeated wrong-secret 404s.

**Why:** Security depends entirely on the operator following the `.env.example` comment ("long random string") and the tunnel URL staying private, with nothing to slow down a brute-force guesser.

**Context:** Found by the `/ship` security specialist review. A startup check (reject a configured secret under ~32 chars) is cheap; rate limiting is more involved and can wait for issue #8 (hardening).

**Effort:** S (startup check) / M (rate limiting)
**Priority:** P3
**Depends on:** None

### The daily call guard still undercounts real dials

**What:** `_calls_today` counts `Negotiation` rows, but a single row can place 2-3 real calls:
`_dial`/`_dial_confirmation` each retry `place_call` up to twice on a `TelephonyError`, and
`_on_confirmation_call_ended`'s no-answer/dropped-call redial (`dial_attempt = 2`) adds another,
neither incrementing the count. With `MAX_CALLS_PER_DAY = N`, actual billed dials can reach
roughly `3N`.

**Why:** `MAX_CALLS_PER_DAY` is the one circuit breaker on real Twilio spend; its docstring and the
`CallGuardBlocked` message ("Daily call limit reached") both claim a precision the counting doesn't
have.

**Context:** Found by the `/ship` adversarial and performance review of owner UI build steps 2-3
(2026-09-18), while fixing the sharper version of this bug on the retry-confirmation route (that
route bypassed the guard entirely by reusing a negotiation row instead of undercounting through
one; see the "Recover from CONFIRMING / CONFIRMATION_FAILED" item above for what shipped). Count
actual `place_call` invocations (e.g. a `call.dialed` audit event `_calls_today` sums) instead of
negotiation rows, or accept the current per-negotiation-row semantics and rename the setting/message
to say what it actually bounds.

**Effort:** M
**Priority:** P2
**Depends on:** None

### Owner-route "local only" trusts headers, never the actual peer

**What:** `LocalOnlyOwnerRoutes` decides everything from `Host`/`Origin`/`cf-connecting-ip` —
client-supplied headers — and never reads `scope["client"]`, the ASGI-reported peer address, even
though it already reads that same field two lines earlier for the `testserver` test-only allowance.

**Why:** Today the real guarantee is uvicorn's default bind to `127.0.0.1`, not this middleware. If
anyone runs `--host 0.0.0.0` (a one-flag mistake, plausible when demoing over Wi-Fi so a judge's
laptop can reach the projector machine), any LAN host sending `Host: localhost:8000` with no
`Origin` gets full owner access — including `/start` and `/approve`, which place real paid calls.
The header checks are solid defense-in-depth against tunnel leakage and DNS rebinding, but nothing
here enforces the "local" the module docstring promises if the bind address is ever widened.

**Why it's separate from the test_client bypass below:** that item is about a specific allowance
being too loosely inferred; this one is about the middleware's whole model never checking the one
signal (the real peer) that would make the guarantee true regardless of headers.

**Context:** Found by the `/ship` adversarial review of owner UI build steps 2-3 (2026-09-18).

**Effort:** S
**Priority:** P2
**Depends on:** None

### `test_client` allowance is inferred from ASGI scope, not an explicit flag

**What:** `LocalOnlyOwnerRoutes.__call__` grants the `testserver` Host allowance based on
`(scope.get("client") or (None, None))[0] == "testclient"` — a value the ASGI server writes into
`scope`, not something the middleware's caller declares.

**Why:** Under uvicorn this is never attacker-controlled, so it's not exploitable today. But the
shipping security boundary depends on which ASGI server sits in front of it: any server or proxy
shim that puts a client-influenced string into `scope["client"]` turns `Host: testserver` into a
bypass. A constructor-level flag the test harness sets explicitly (`LocalOnlyOwnerRoutes(app,
allow_testserver=True)`) doesn't have this property.

**Context:** Found by the `/ship` adversarial review of owner UI build steps 2-3 (2026-09-18).

**Effort:** S
**Priority:** P3
**Depends on:** None

### `/approve` and `/retry-confirmation` block the event loop on a real dial

**What:** Both routes are `async def` and `await` a chain ending in `telephony.place_call` (up to
two attempts, `httpx.AsyncClient(timeout=httpx.Timeout(10.0))` each). Worst case, the owner's click
hangs for ~20s before the response returns, during which the surrounding synchronous SQLAlchemy
work (including the N+1 in `_build_view`) also runs on the loop.

**Why:** A single slow request can stall Twilio webhook callbacks for a *different*, concurrently
in-progress call (Twilio's own webhook timeout is roughly 15s), and the owner UI's mutation
`fetch()` in `web/src/api/client.ts` has no timeout on this path (unlike the poll's
`REQUEST_TIMEOUT_MS`), so "Updating…" can sit for the full window with no way to cancel.

**Context:** Found by the `/ship` adversarial review of owner UI build steps 2-3 (2026-09-18).
Fix direction: return 202 right after recording the decision and hand the dial to a background
task, letting the existing 1s poll surface `CONFIRMING`/`CONFIRMATION_FAILED` — matches how `/start`
already treats dialing as fire-and-poll rather than fire-and-wait.

**Effort:** M
**Priority:** P3
**Depends on:** None

### Eagerly-built Twilio client leaks its HTTP connection pool outside the live path

**What:** `app/main.py`'s module-level `app = create_app()` builds a real `TwilioVoiceProvider`
(and its `httpx.AsyncClient`) from `.env` at import time, before any lifespan runs `aclose()` on
it. Both `app/openapi_export.py` and `scripts/e2e_server.py` import `app.main`, so each acquires
(and never closes) a Twilio client's connection pool purely as a side effect of importing the
module, even though neither ever dials.

**Why:** `openapi_export.py`'s own comment claims "the app is built offline with no .env" — true
for the app it renders, false for the module-level one it imports alongside it, which makes
`uv run python -m app.openapi_export` (and the pytest that checks the snapshot is fresh) fragile
against a missing or malformed `.env`.

**Context:** Found by the `/ship` adversarial review of owner UI build steps 2-3 (2026-09-18).

**Effort:** S
**Priority:** P3
**Depends on:** None

### Simplification: five spots reimplement something already available

**What:** `/ship`'s simplification specialist found five places doing by hand what the platform,
runtime or an existing import already does — `web/vite.config.ts`'s ~25-line proxy error handler
duplicates Vite 8's own default 502 response; `app/orchestrator.py::start_confirmation`'s
`guard_checked` flag exists for a hypothetical caller that doesn't exist (the one production call
site always passes `True`); `web/src/api/client.ts`'s manual `AbortController`/`setTimeout`/
`clearTimeout` timeout plumbing duplicates `AbortSignal.timeout` + `AbortSignal.any` (both
available in the pinned Node/jsdom); `web/src/App.tsx`'s `nairobiToday()` builds an ISO date by
hand when the `en-CA` `Intl.DateTimeFormat` it already calls formats as `YYYY-MM-DD` directly; and
the now-removed `CONFIRM_RETRY_WAIT` state (fixed in this same review, see above).

**Why:** None of these are bugs — they're ~46 lines of code doing what a one-line call already
would, each an extra thing to maintain and read past.

**Context:** Found by the `/ship` simplification specialist on owner UI build steps 2-3
(2026-09-18). Advisory-only lens; never auto-applied.

**Effort:** S
**Priority:** P3
**Depends on:** None

### e2e_server ships an unauthenticated arbitrary-transition route

**What:** `scripts/e2e_server.py`'s `POST /__e2e/transactions/{id}/transition` bypasses every
policy check and is reachable by anything that satisfies the loopback middleware. It lives under
`scripts/`, not `tests/`, so nothing in packaging excludes it.

**Why:** Contained today by the `_DB_DIR` guard (refuses to run against anything but a throwaway
temp DB) and the default `127.0.0.1` bind — the same two things the peer-address TODO above says
aren't a complete guarantee.

**Context:** Found by the `/ship` adversarial review of owner UI build steps 2-3 (2026-09-18).

**Effort:** S
**Priority:** P3
**Depends on:** None

## Infrastructure

### Run the test suites in CI on every push and PR

**What:** Add a GitHub Actions workflow that runs `uv run pytest`, `uv run ruff check . && uv run ruff format --check .`, then in `web/` `npm ci && npm test && npm run build`, and the Playwright E2E.

**Why:** The repo has no CI at all (`.github/workflows/` doesn't exist). Every suite runs only on whichever laptop remembers to run it, so a push can break `main`, and it's a public submission repo (#9).

**Context:** Found by `/plan-eng-review` of owner UI build step 1 (2026-09-14). Every test is offline by design (`FakeTelephonyProvider`, `ScriptedLLMProvider`, no secrets), so the workflow needs no credentials. Start from the Commands table in CLAUDE.md and install the toolchain with mise (`mise.toml` pins Python, uv and Node). The Playwright lane needs its browser installed in CI, which makes it the slow lane. It also catches a stale `web/openapi.json` snapshot, which a pytest guards.

**Effort:** S
**Priority:** P2
**Depends on:** Owner UI build step 1 landing (the web/ tests and E2E exist then)

### Keep pyproject, package.json and FastAPI versions in step with VERSION

**What:** Make `VERSION` the single source for the project's version numbers. Add a pytest that checks `pyproject.toml` (3-digit), `web/package.json` and the FastAPI app version all match it, or document a manual sync step in the release flow.

**Why:** PR #20 bumped `VERSION` to `0.2.1.0`, but `pyproject.toml` still says `0.2.0` (so does `uv.lock`), and `/openapi.json` reports `0.1.0`. With `web/package.json` that's three drifting version fields, and it's unclear which build is on the demo laptop.

**Context:** Found by `/plan-eng-review` of owner UI build step 1 (2026-09-14). `/ship`'s `gstack-version-bump` only syncs `package.json`, and it can't run here without `bun`. A pyproject bump needs `uv lock` afterwards. The committed `web/openapi.json` already carries the FastAPI version (`0.1.0`), so fixing this means regenerating it with `uv run python -m app.openapi_export`.

**Effort:** S
**Priority:** P3
**Depends on:** Owner UI build step 1 (web/package.json exists)

### Index the tables the per-poll view reads

**What:** Add `Index("ix_audit_tx_type", AuditEvent.transaction_id, AuditEvent.event_type)`,
`Index("ix_transcript_turns_negotiation", TranscriptTurn.negotiation_id)`,
`Index("ix_offers_negotiation", Offer.negotiation_id)` and
`Index("ix_negotiations_transaction", Negotiation.transaction_id)` in `app/models.py`, then delete
`transaction_agent.db` and re-seed. There are no migrations, and `create_all` won't add an index to
an existing table.

**Why:** `_build_view` reads a transaction's negotiations, their transcript turns and offers, and
its `status.changed` rows, on every 1-second poll. None of these foreign-key columns is indexed
(SQLite doesn't index them automatically), so every one of those queries scans the whole table.
Negligible at demo scale, but grows with every rehearsal left in the file, and `_build_view` also
runs one query per negotiation rather than a bulk `IN` select — an N+1 on the same hot path.

**Context:** The audit_events half found by the `/ship` performance specialist and adversarial pass
on owner UI build step 1 (2026-09-16); the transcript_turns/offers/negotiations half and the N+1 by
the `/ship` performance specialist on owner UI build steps 2-3 (2026-09-18), which also measured
the `status.changed` query as deliberately uncapped (unlike the adjacent audit-log query's
`.limit(50)`) and serialized whole into every response body. The owner chose to defer all of it, to
avoid a DB reset mid-build.

**Effort:** S
**Priority:** P3
**Depends on:** A good moment to reset the local database

### The font bundle ships subsets this UI never renders

**What:** `web/src/main.tsx`'s six `@fontsource` imports (IBM Plex Sans/Mono, 400/500/600/700) pull
every language subset and both `.woff`/`.woff2` formats. Measured: `web/dist` is 1.2MB, of which
840KB is fonts — 423KB is cyrillic/cyrillic-ext/greek/vietnamese subsets a Nairobi photography UI
never renders, and 396KB is legacy `.woff` duplicates of the `.woff2` files already present.

**Why:** `unicode-range` means the browser skips unmatched subsets at runtime, so this mostly costs
disk and build time rather than what a viewer downloads — but the artifact FastAPI serves is ~3x
larger than it needs to be for no benefit on this project.

**Context:** Found by the `/ship` performance specialist on owner UI build steps 2-3 (2026-09-18).
Fix: import the latin-only entrypoints (`@fontsource/ibm-plex-sans/400-latin.css`, etc.) instead of
the unscoped ones.

**Effort:** S
**Priority:** P3
**Depends on:** None

### CallPanel/RecommendationCard don't match DESIGN.md's typography, color, and copy spec

**What:** `/ship`'s design specialist found 8 DESIGN.md violations in the two new components
(the layout bug that could push Approve off-screen was the 9th finding and is fixed -- see the
`.transaction-layout` grid-area/flex rework in `web/src/styles.css`):

- **Approve's label has no overage.** DESIGN.md: "when `final_price` is above `tx.max_budget`,
  the Approve label carries the overage, `Approve KES 23,000 · KES 3,000 over your cap`". The
  button just says "Approve".
- **The final price has no visual weight.** DESIGN.md: base is 18px except "the final price
  (44px bold), the provider name (24px bold)". Both render as ordinary `dl` values today.
- **The transcript spends `--accent` on decoration.** `.transcript-agent { border-left-color:
  var(--accent) }` — DESIGN.md reserves `--accent` for "the one primary action (Approve), and
  nothing else".
- **Highlighted transcript amounts are amber**, but DESIGN.md: "amber means 'needs your approval'
  on this screen" and specifies highlighted amounts as "`--text` semibold", no ground.
- **The transcript rows are a colored-left-border card list**, not DESIGN.md's specified "96px
  uppercase mono speaker column" with no background/border.
- **The `REQUIRES_APPROVAL`/`WITHIN_LIMIT` state is plain amber/green text**, not the pill with an
  inline SVG icon (▲/✓) DESIGN.md specifies (`StatusTimeline.tsx` already has the SVG-not-glyph
  pattern to reuse).
- **`--amber-edge` is declared and never used.** DESIGN.md: "the approval styling (the pill and an
  `--amber-edge` border) shows only while `approve` or `decline` is in `tx.allowed_actions`".
- **CallPanel's Duration is a frozen word ("In progress"), not a ticking timer.** DESIGN.md gives
  CallPanel "a ticking timer off `answered_at`"; `web/src/useTick.ts` already exists and is used
  by `StatusTimeline`.

**Why:** This is the one screen judges watch for the whole two-minute demo; none of these are
functional bugs, but the price that decides the entire scenario (KES 23,000 against a KES 20,000
cap) currently looks like any other label, and the accent color that's supposed to mean one thing
("click here") appears on transcript rows that aren't clickable.

**Context:** Found by the `/ship` design specialist on owner UI build steps 2-3 (2026-09-18). The
owner chose to fix only the layout bug (a functional risk) in that ship and defer the rest to a
`/design-review` pass, since it's real visual/CSS work distinct from the security and correctness
fixes that shipped alongside it.

**Effort:** M
**Priority:** P3
**Depends on:** None

## Docs

### Reconcile README's product story with what the backend accepts

**What:** README pitches "Nego", a general procurement agent ("500kg of produce. Maximum KES 80,000"). The backend only accepts photography (`TransactionCreate.service` is `Literal["photography"]`), and CLAUDE.md's canonical scenario is a photographer at KES 20,000. On 2026-09-14 the owner UI's fixtures were set to follow the backend. Decide which story the submission tells, and make README, the demo script and the submission copy agree.

**Why:** A judge who reads the README and then watches the demo sees two different products. The README's own example negotiation (vegetables, KES 95,000 → 80,000) cannot be run by this code.

**Context:** This is what's left of the original "Fix README's setup section" item. The mechanical half of that item was done by the doc-sync on 2026-09-18 (v0.3.0.0): the setup now uses `mise install` / `uv sync` / `npm ci` instead of a `requirements.txt` that doesn't exist, the Python pin says 3.12, the duplicated uvicorn line is gone, the repo tree matches the real module layout, and the README gained an Owner UI section and a test/run section that match CLAUDE.md's Commands table.

What remains is the narrative call, which a doc-sync must not make on its own: either narrow the README's pitch to the photography slice the code actually serves, or keep procurement as the vision and mark the prototype's single supported category explicitly, in both README and the submission.

**Effort:** S
**Priority:** P2
**Depends on:** part of issue #9

## Completed

### Check Host and Origin on owner routes (DNS rebinding can place real calls)

**What:** Owner routes should accept only a local `Host` (`localhost`, `127.0.0.1`, `[::1]`, with a port). `/webhooks/*` keeps accepting the tunnel host. POSTs with a foreign `Origin` should be rejected.

**Why:** `app/middleware.py` treats any request without `cf-connecting-ip` as local. A `/ship` adversarial probe got `POST /transactions` with `Host: attacker.example` → 201, and a cross-origin form POST to `/transactions/{id}/start` → 202, which dials. A page using DNS rebinding in the owner's browser could create and start transactions while the API runs, placing real paid Twilio calls, capped only by `max_calls_per_day`.

**Context:** Found by the `/ship` review of owner UI build step 1 (2026-09-16). `LocalOnlyOwnerRoutes` now checks `_is_loopback_host` (Host) and `_is_loopback_origin` (Origin, on state-changing methods and WebSocket handshakes), with `/webhooks/*` exempt.

**Completed:** v0.3.0.0 (2026-09-18)

### Move test phone numbers out of the +2547 range

**What:** Change `tests/test_orchestrator.py`'s `+25471100000x` provider numbers to the `+2541…` range that CLAUDE.md prescribes.

**Why:** #9's pre-publish secret check greps for `+2547\d{8}`, and these fake numbers match it. The check can't pass cleanly while they're there.

**Context:** Found while fixing the same problem in `tests/test_api.py` and `tests/test_main_web.py` during the `/ship` of owner UI build step 1 (2026-09-16).

**Completed:** v0.3.0.0 (2026-09-18)
