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

### Distinguish STT failures from caller silence in the audit trail

**What:** `AfricasTalkingVoiceProvider._transcribe` swallows any speech-to-text exception (auth, network, quota) and returns `""`, which `CallCoordinator` then treats as ordinary caller silence. `app/audit.py`'s `EVENT_TYPES` has no distinct type for it, and no new module in this diff calls `record_event` at all yet.

**Why:** A misconfigured `GOOGLE_APPLICATION_CREDENTIALS` during a live demo would look identical to the caller just not speaking — the agent repeats "could you say that again?" with nothing in the audit trail to tell the two apart.

**Context:** Found by the `/ship` adversarial review. Wiring `record_event` calls into the call-handling path is naturally part of issue #6 (persistence/orchestration); add a `speech.failed`-style event type to the closed set in `app/audit.py` at the same time.

**Effort:** M
**Priority:** P2
**Depends on:** Issue #6

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

## Completed
