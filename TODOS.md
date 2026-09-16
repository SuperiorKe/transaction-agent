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

### Check Host and Origin on owner routes (DNS rebinding can place real calls)

**What:** Owner routes should accept only a local `Host` (`localhost`, `127.0.0.1`, `[::1]`, with a port). `/webhooks/*` keeps accepting the tunnel host. POSTs with a foreign `Origin` should be rejected.

**Why:** `app/middleware.py` treats any request without `cf-connecting-ip` as local. A `/ship` adversarial probe got `POST /transactions` with `Host: attacker.example` → 201, and a cross-origin form POST to `/transactions/{id}/start` → 202, which dials. A page using DNS rebinding in the owner's browser could create and start transactions while the API runs, placing real paid Twilio calls, capped only by `max_calls_per_day`.

**Context:** Found by the `/ship` review of owner UI build step 1 (2026-09-16). This predates the owner UI; the UI only makes the browser the main client. Starlette's `TrustedHostMiddleware` covers the Host half, but it applies to every path, so it must exempt `/webhooks/*` or live inside `LocalOnlyOwnerRoutes`. The existing tests use `TestClient`'s default `testserver` host, so they need an allowed test host. Add tests for foreign Host, foreign Origin, local Origin and tunnel webhooks.

**Effort:** S
**Priority:** P1
**Depends on:** None; must land before owner UI build step 2 (the Start button)

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

### Expose the confirmation half of the state machine over HTTP

**What:** No HTTP route calls `start_confirmation()` or `retry_confirmation()`, so
`APPROVED -> CONFIRMING -> CONFIRMED` is unreachable through the API even though both orchestrator
functions are written and unit-tested. Meanwhile `ALLOWED_ACTIONS_BY_STATUS` still advertises
`retry_confirmation` for `CONFIRMATION_FAILED` and `CONFIRM_RETRY_WAIT`.

**Why:** The API tells clients an action is allowed and offers nowhere to send it. The owner UI
(#7) is deliberately `allowed_actions`-driven — it renders buttons from what the server says is
legal rather than re-deriving the state machine client-side — so it will faithfully render a button
that 404s. Four of the eighteen statuses (`CONFIRMING`, `CONFIRM_RETRY_WAIT`, `CONFIRMED`,
`CONFIRMATION_FAILED`) and one whole component of #7 are dead surface until this lands, and
`APPROVED` is a non-terminal dead end.

**Context:** Found while designing the owner UI; see `DESIGN.md` ("Known backend gaps").
`start_confirmation(session, tx, *, telephony, max_calls_per_day, now)` raises `ApprovalRequired`
unless an APPROVED approvals row exists for the current recommendation's `offer_id`.
`retry_confirmation(session, tx, negotiation, *, telephony)` takes the negotiation, so a route must
resolve the latest `kind="confirmation"` negotiation for the transaction itself. `approve_transaction`
in `app/routes/transactions.py` carries a comment marking this as issue #6 priority 4 (stretch), so
it's deferred rather than forgotten. Note also that nothing calls `schedule_confirmation_retry`, so
`CONFIRM_RETRY_WAIT` is never entered in production; and when a `scheduler` is passed, the callback
it hands over is `lambda: None`, so wiring a real timer would still retry nothing. Fix both when
wiring the retry, or drop the status from the flow. Cheapest correct interim fix if the full wiring stays out
of scope: drop `retry_confirmation` from `ALLOWED_ACTIONS_BY_STATUS` so the API stops advertising an
action it cannot serve.

**Effort:** M
**Priority:** P2
**Depends on:** None (the orchestrator functions already exist and are tested)

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

### Index audit_events for the per-poll status history

**What:** Add `Index("ix_audit_tx_type", AuditEvent.transaction_id, AuditEvent.event_type)` in `app/models.py`, then delete `transaction_agent.db` and re-seed. There are no migrations, and `create_all` won't add an index to an existing table.

**Why:** `_build_view` now reads a transaction's `status.changed` rows on every 1-second poll. `audit_events` has no index on either column, and SQLite doesn't index foreign keys automatically, so each poll scans the whole table. That's negligible at demo scale (a few thousand rows), but grows with every rehearsal left in the file.

**Context:** Found by the `/ship` performance specialist and adversarial pass on owner UI build step 1 (2026-09-16). The owner chose to defer it, to avoid a DB reset mid-build.

**Effort:** S
**Priority:** P3
**Depends on:** A good moment to reset the local database

### Move test phone numbers out of the +2547 range

**What:** Change `tests/test_orchestrator.py`'s `+25471100000x` provider numbers to the `+2541…` range that CLAUDE.md prescribes.

**Why:** #9's pre-publish secret check greps for `+2547\d{8}`, and these fake numbers match it. The check can't pass cleanly while they're there.

**Context:** Found while fixing the same problem in `tests/test_api.py` and `tests/test_main_web.py` during the `/ship` of owner UI build step 1 (2026-09-16). `app/routes/transactions.py::_mask_phone`'s docstring example (`'+254712345678'`) is documentation; decide whether #9's check should skip it.

**Effort:** S
**Priority:** P2
**Depends on:** None

## Docs

### Fix README's setup section

**What:** Rewrite README's setup from CLAUDE.md's Commands table (`mise install`, `uv sync`, seed, uvicorn, and the `web/` `npm ci` / build steps), and correct the repo tree.

**Why:** It's the first thing judges and teammates read, and following it fails on the very first step. The submission (#9) requires a README that covers setup.

**Context:** Logged as documentation debt by the doc-sync on PR #20:
- It says `pip install -r requirements.txt`, but there's no `requirements.txt` (the project uses uv and `pyproject.toml`).
- It asks for Python 3.10+, but the project pins 3.12.
- It lists the uvicorn command twice.
- Its tree shows `app/policy/`, but the code is `app/policy.py`.
- Its pitch is "Nego", a procurement agent ("500kg of produce. Maximum KES 80,000"). The backend only accepts photography (`TransactionCreate.service` is `Literal["photography"]`), and CLAUDE.md's canonical scenario is a photographer at KES 20,000. On 2026-09-14 the owner UI's fixtures were set to follow the backend. Reconcile the product story here, together with the submission copy.

The doc-sync skipped it because it's a rewrite of more than 10 lines. Do it after build step 1, so the web/ commands are real.

**Effort:** S
**Priority:** P2
**Depends on:** Owner UI build step 1; part of issue #9

## Completed
