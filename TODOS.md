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

**What:** Done. `app/agent/session.py` `respond()` now commits right after `_write_transcript("provider", ...)`, before `await self._run(...)`. A poll sees what the provider said while the agent's reply is still generating.

**Why:** Previously `_write_transcript` only reached the database at the `self._session.commit()` after the model returned, so the owner UI's transcript lagged a full model round behind the call.

**Context:** Found by the outside-voice pass of `/plan-eng-review` on owner UI build step 1 (2026-09-14). The lock theory was verified, not assumed: two threads against a file-backed SQLite DB showed a flush-without-commit both (a) invisible to a concurrent reader and (b) blocking a concurrent writer for the full hold period; committing immediately made both near-instant. A model-call failure after the provider-turn commit is fine as-is -- the transcript row is what was actually said, independent of whether the reply generates successfully.

**Effort:** S
**Priority:** P1
**Depends on:** None; must land before owner UI build step 3 (CallPanel)
**Completed:** unreleased (2026-09-19)

### Recover from CONFIRMING / CONFIRMATION_FAILED with no owner-facing way out

**What:** Done, all three gaps.
- `CONFIRMING` no longer strands the owner if a Twilio callback is lost: `app/orchestrator.py`'s
  `maybe_timeout_confirming()` runs on every `GET /transactions/{id}` (which the owner UI already
  polls every second) and moves `CONFIRMING` -> `CONFIRMATION_FAILED` once
  `Settings.confirmation_stuck_timeout_seconds` (default 120) has passed since the confirmation
  negotiation was created with no callback advancing it. No scheduler needed or added, matching
  CLAUDE.md's "Workflow: Plain asyncio tasks".
- `CONFIRMATION_FAILED` now advertises `decline` alongside `retry_confirmation`. It's wired to the
  existing `decline()`/`DECLINED -> CLOSED` path via a new `(CONFIRMATION_FAILED, DECLINED)` state
  transition -- an abandon after a failed confirmation call reuses the ordinary decline machinery,
  so the audit trail reads APPROVED then DECLINED for that offer.
- `APPROVED` now advertises `retry_confirmation` too, recovering the case where an exception fires
  between recording approval and the confirmation dial (an unset `current_provider_id`, a DB
  error) -- `retry_confirmation_transaction` treats `APPROVED` as "start fresh via
  `start_confirmation()`" rather than "resolve a confirmation negotiation to retry", since none
  was ever created. `POST /approve` itself is unchanged: an unexpected exception there still
  surfaces as a 500 to that request, but the transaction is no longer stranded -- the next poll
  shows `APPROVED` with `retry_confirmation` advertised.

**Why:** A stuck `CONFIRMING` or a `CONFIRMATION_FAILED` an owner doesn't want to keep retrying are
both dead ends the owner UI can only render as "waiting" forever.

**Context:** Found by the `/ship` adversarial review of owner UI build steps 2-3 (2026-09-18).
Tests: `tests/test_orchestrator.py` (`maybe_timeout_confirming`, `CONFIRMATION_FAILED -> DECLINED`)
and `tests/test_api.py` (the lazy poll timeout, decline-as-abandon, retry-from-APPROVED) plus the
new `ALLOWED_TRANSITIONS` row is covered automatically by the existing parametrized
`test_allowed_transition_updates_status_and_audits` in `tests/test_states.py`.

**Effort:** M
**Priority:** P1
**Depends on:** None
**Completed:** unreleased (2026-09-19)

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

**What:** Done. `app/middleware.py`'s `_is_loopback_peer()` checks `scope["client"]`, the
ASGI-reported peer address, and `LocalOnlyOwnerRoutes.__call__` now forbids a request whose peer
isn't loopback (`127.0.0.1`/`::1`, or the `testclient` sentinel when the existing
`allow_testserver` inference already permits it) alongside the existing Host/Origin/tunnel checks.
`client is None` (a transport with no peer tuple, e.g. a Unix socket) still passes this specific
check -- nothing to verify there -- and relies on the header checks alone, same as before.

**Why:** Previously the real guarantee was uvicorn's default bind to `127.0.0.1`, not this
middleware. If anyone ran `--host 0.0.0.0` (a one-flag mistake, plausible when demoing over Wi-Fi
so a judge's laptop can reach the projector machine), any LAN host sending `Host: localhost:8000`
with no `Origin` got full owner access — including `/start` and `/approve`, which place real paid
calls. The header checks were solid defense-in-depth against tunnel leakage and DNS rebinding, but
nothing enforced the "local" the module docstring promises if the bind address was ever widened.

**Why it's separate from the test_client bypass below:** that item is about a specific allowance
being too loosely inferred; this one was about the middleware's whole model never checking the one
signal (the real peer) that makes the guarantee true regardless of headers. Left the
`test_client`/`allow_testserver` inference mechanism itself untouched -- fixing it would mean
threading an explicit flag through `create_app()` and updating every `TestClient(create_app())`
call site across the test suite, for a risk the item's own text already calls "not exploitable
today" (uvicorn, the only ASGI server this project targets, never puts attacker-controlled data in
`scope["client"]`). Deferred as its own item below rather than bundled into this fix.

**Context:** Found by the `/ship` adversarial review of owner UI build steps 2-3 (2026-09-18).
Tests: `tests/test_middleware.py` (a real loopback peer with a non-TestClient scope passes; a
non-loopback peer is forbidden even with a passing `Host` header, both via a hand-built ASGI scope
and via `httpx.ASGITransport(client=...)` through the real app).

**Effort:** S
**Priority:** P2
**Depends on:** None
**Completed:** unreleased (2026-09-19)

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

**What:** Done. `TwilioVoiceProvider` (`app/telephony/twilio.py`) now opens its owned
`httpx.AsyncClient` lazily -- on first actual network call (`place_call`/`hang_up`), via a `_http`
property backed by `_lazy_http: httpx.AsyncClient | None = None` -- instead of in `__init__`.
Construction, `render()`/`acknowledge()` (pure TwiML, no network), and `aclose()` on a
never-dialled provider now never open a connection pool at all.

**Why:** `app/main.py`'s module-level `app = create_app()` builds a `TwilioVoiceProvider` from
`.env` at import time, before any lifespan runs `aclose()` on it. Both `app/openapi_export.py` and
`scripts/e2e_server.py` import `app.main`, so each used to acquire (and never close) a real
connection pool purely as a side effect of importing the module, even though neither ever dials.
`openapi_export.py`'s own comment claims "the app is built offline with no .env" — true for the
app it renders, false for the module-level one it imports alongside it.

**Context:** Found by the `/ship` adversarial review of owner UI build steps 2-3 (2026-09-18).
Fixed at the source (the provider itself) rather than restructuring `create_app()`'s wiring to
defer telephony construction into the lifespan -- smaller, self-contained, and the module-level
`app = create_app()` line can't be made lazy anyway without changing the documented
`uvicorn app.main:app` invocation. Test:
`test_the_owned_http_client_is_never_opened_until_actually_needed` in
`tests/test_telephony_twilio.py`.

**Effort:** S
**Priority:** P3
**Depends on:** None
**Completed:** unreleased (2026-09-19)

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

**What:** Done. `web/src/main.tsx`'s six `@fontsource` imports now use the latin-subset entrypoints
(`@fontsource/ibm-plex-sans/latin-{400,500,600,700}.css`,
`@fontsource/ibm-plex-mono/latin-{400,600}.css`) instead of the unscoped ones that also pulled
cyrillic/cyrillic-ext/greek/vietnamese `@font-face` blocks. Measured: `web/dist` dropped from
1.2MB to 516KB (12 font files instead of dozens).

**Why:** `unicode-range` means the browser skips unmatched subsets at runtime, so this mostly cost
disk and build time rather than what a viewer downloaded — but the artifact FastAPI serves was ~3x
larger than it needed to be for no benefit on this project.

**Context:** Found by the `/ship` performance specialist on owner UI build steps 2-3 (2026-09-18).
Verified the latin subset still covers every non-ASCII character this UI actually renders (the
bullet `•` in masked phone numbers, the middle dot `·` in the Approve overage label): the combined
file's own `unicode-range` for the latin block is `U+0000-00FF,...,U+2000-206F,...`, and
`U+2000-206F` (General Punctuation) covers both -- confirmed by inspecting
`node_modules/@fontsource/ibm-plex-sans/400.css`'s per-subset `unicode-range` declarations, since
the single-subset `latin-*.css` files omit the attribute entirely (no other subset registered to
compete against). `npm test` (199), `npm run build`, and `npm run e2e` (14, including the fixture
layout checks that would catch a glyph-fallback reflow) all still pass.

**Effort:** S
**Priority:** P3
**Depends on:** None
**Completed:** unreleased (2026-09-19)

### CallPanel/RecommendationCard don't match DESIGN.md's typography, color, and copy spec

**What:** Done, all 8. `/ship`'s design specialist found 8 DESIGN.md violations in the two new
components (the layout bug that could push Approve off-screen was the 9th finding and was fixed
separately -- see the `.transaction-layout` grid-area/flex rework in `web/src/styles.css`):

- **Approve's label has no overage.** Fixed: `RecommendationCard.tsx`'s `ApproveLabel` always
  shows `Approve KES {price}`, plus a second label line (`.approve-overage`, block-level) with
  the overage when `final_price` is above `tx.max_budget`.
- **The final price has no visual weight.** Fixed: `.recommendation-price` (44px bold) on the
  price `<dd>`, `.call-provider-name` (24px bold) on CallPanel's provider `<dd>`.
- **The transcript spends `--accent` on decoration.** Fixed by the row-layout rework below --
  `.transcript-turn` no longer has a border at all.
- **Highlighted transcript amounts are amber.** Fixed: `mark` is now `--text` semibold, no
  background.
- **The transcript rows are a colored-left-border card list.** Fixed: `.transcript-turn` is now a
  96px-column CSS grid (speaker | text), no background or border; `agent` renders `--text` bold,
  `provider` `--muted` (the shared rule), `system` turns render with no speaker label and italic
  `--muted` text.
- **The `REQUIRES_APPROVAL`/`WITHIN_LIMIT` state was plain amber/green text.** Fixed: a
  `PolicyPill` with an inline SVG icon (▲/✓, reusing `StatusTimeline`'s SVG-not-glyph pattern,
  not its component -- the icon set differs). Copy also corrected to match DESIGN.md's exact
  text ("Within limit" / "Requires approval", not "Within your limit" / "Requires your approval").
  Bundled with this: the header now reads "Recommendation · accept/ask you/decline" per
  `recommendation.recommendation`, per the same DESIGN.md sentence.
- **`--amber-edge` was declared and never used.** Fixed: `.recommendation-card-pending-approval`
  applies it, and both the pill and the border now show only while `approve` or `decline` is in
  `tx.allowed_actions` and `policy_status === "REQUIRES_APPROVAL"` -- they disappear once a
  decision is made (APPROVED, CLOSED, ...), while the price/provider/reason row survives.
- **CallPanel's Duration was a frozen word.** Fixed: ticks 1 s off `answered_at` via
  `useTick` while `duration_seconds` is null and the negotiation's `status` is `IN_PROGRESS`;
  freezes to the persisted `duration_seconds` once set, and to "In progress" if never answered or
  answered but not live (a missed end callback must not tick forever).

Surfaced along the way: `RecommendationCard`'s Approve button was already rendering (and would
have dialed) even when the local offer join failed to resolve `recommendation.offer_id` against
`tx.negotiations[].offers[]` -- previously invisible because the button carried no price to
contradict the "no longer available" message next to it. `recommendation.final_price` is
authoritative server data independent of that join, so the button showing it isn't "inventing" a
price; the facts list (`.recommendation-price`) still correctly shows nothing in that state.

**Why:** This is the one screen judges watch for the whole two-minute demo; none of these were
functional bugs, but the price that decides the entire scenario (KES 23,000 against a KES 20,000
cap) used to look like any other label, and the accent color that's supposed to mean one thing
("click here") used to appear on transcript rows that aren't clickable.

**Context:** Found by the `/ship` design specialist on owner UI build steps 2-3 (2026-09-18).
Fixed CSS-first per DESIGN.md's "Visual system" and "Component contracts" sections. Tests:
`web/src/components/CallPanel.test.tsx` (provider weight, live-tick via fake timers, freeze
behavior already covered) and `RecommendationCard.test.tsx` (pill copy and visibility, amber-edge
border, header suffix, no-overage-within-cap, the offer-join edge case above).

**Effort:** M
**Priority:** P3
**Depends on:** None
**Completed:** unreleased (2026-09-19)

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
