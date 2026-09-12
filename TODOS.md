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

### Webhook secret entropy + basic abuse throttling

**What:** `VOICE_WEBHOOK_SECRET` is the sole authentication for `/webhooks/voice/{secret}` (AT doesn't sign requests). Nothing validates its length/entropy at startup, and there's no rate limiting or backoff on repeated wrong-secret 404s.

**Why:** Security depends entirely on the operator following the `.env.example` comment ("long random string") and the tunnel URL staying private, with nothing to slow down a brute-force guesser.

**Context:** Found by the `/ship` security specialist review. A startup check (reject a configured secret under ~32 chars) is cheap; rate limiting is more involved and can wait for issue #8 (hardening).

**Effort:** S (startup check) / M (rate limiting)
**Priority:** P3
**Depends on:** None

## Completed
