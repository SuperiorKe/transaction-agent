# Changelog

All notable changes to this project are documented here.
Versions use the 4-digit `MAJOR.MINOR.PATCH.MICRO` format from `VERSION`.

## [0.3.0.0] - 2026-09-18

### Added
- **The owner UI can now compose and start a transaction, and watch it through to a confirmed booking, from a single screen.**
  - The empty state is a request composer: type a free-text request ("Photographer for Monday in Nairobi. Max KES 20,000."), review and correct the date/location/cap/attempts it parsed out, then create and start the call in one action.
  - A live-call panel shows the newest negotiation or confirmation call in progress — provider, masked phone number, call state, duration, counteroffers, and the transcript, with recorded amounts highlighted only when they match a real persisted offer (never a number the model merely said in passing).
  - A recommendation card shows the offer, the policy status, and Approve/Decline/Retry — rendered only when the API's `allowed_actions` actually advertises them, never guessed client-side.
- **Approving an offer now starts the confirmation call.** `POST /transactions/{id}/approve` validates the offer against the current recommendation, checks the daily call guard before recording the decision (so a guard block leaves the decision actionable rather than stranding it), then dials the provider back to confirm the terms on a dedicated confirmation-call script — distinct from the negotiation call, so the agent doesn't try to renegotiate a price it has no authority to change.
- **A failed confirmation can be retried from the UI.** `POST /transactions/{id}/retry-confirmation` redials on the owner's explicit request, on a fresh call record so the daily call guard, the call timer, and the outcome of a previous attempt can never bleed into the retry.
- **Owner routes now reject more than just the tunnel.** Beyond the existing tunnel-header check, a request must carry a loopback `Host` and, for any state-changing request, a loopback `Origin` — closing a DNS-rebinding path that could otherwise let a page in the owner's browser start real paid calls.

### Fixed
- **The Approve/Decline buttons could render below the visible 1280×720 screen** on a longer call or a longer recommendation reason — exactly the shape of the canonical demo scenario (a KES 23,000 quote against a KES 20,000 cap). The whole screen now bounds itself to one viewport with no page scroll, and any card whose own content runs long scrolls internally instead of pushing the actions off screen.
- **A malformed or unusual status/audit row could 500 the one screen that shows a transaction**, instead of degrading gracefully.
- **A hostless request whose ASGI `client` scope was `None`** (a real, spec-legal case) crashed the owner-route middleware instead of being rejected as not local.

### Changed
- The unused, unreachable `CONFIRM_RETRY_WAIT` status and its scheduled-retry machinery were removed from the state machine; confirmation retry is a manual, owner-triggered action only.

## [0.2.1.0] - 2026-09-14

### Added
- **The owner UI has an approved look.** DESIGN.md now sets out the colours, fonts, spacing and component styles for the screen judges watch during the demo, so whoever builds `web/` starts from a decided design instead of picking their own:
  - It was chosen from three real 1280×720 mockups of the approval moment (a KES 23,000 quote against a KES 20,000 cap).
  - The chosen look is a dark "Control Room" theme: IBM Plex type, a cyan main action, and amber (always with a text label) for anything that needs your approval.
  - It also lists what to check on the real projector at rehearsal, and what to change if the dark background washes out.

## [0.2.0.0] - 2026-09-12

### Added
- **The budget rules are enforced in code, not the prompt (#3):**
  - Counteroffers are calculated deterministically: on a KES 20,000 cap, the agent offers 19,500, then 20,000.
  - Accept / escalate / stop decisions and recommendation reasons are deterministic too.
  - A spoken-price check rejects any amount the provider didn't actually say.
- **Real phone calls through Africa's Talking Voice:**
  - outbound calls
  - a callback webhook protected by a secret path
  - turn-by-turn `<Say>` + `<Record>` voice XML
  - recording downloads limited to allowlisted hosts and capped in size
- **Caller speech transcribed by Google Cloud Speech-to-Text v2** (`chirp_3` in `eu`). Africa's Talking MP3 recordings are sent as-is.
- **The negotiation agent runs on the Anthropic Messages API:**
  - The model comes from `ANTHROPIC_MODEL` (default `claude-opus-5`, low effort, server-side refusal fallbacks).
  - It runs in a provider-neutral engine loop with a bounded number of tool rounds.
- **Every vendor sits behind an interface** (`TelephonyProvider`, `SpeechToText`, `LLMProvider`) with an in-memory fake:
  - The whole test suite runs offline, with no phone number or API keys.
  - An architecture test keeps vendor code out of the agent and policy modules.
- **Each call gets its own agent** through a call coordinator. It asks the caller to repeat once on silence and ends the call after a second silent turn.
- **Credential checks:** `uv run python -m app.llm.anthropic` confirms your key can use the configured model, and `uv run pytest -m live` runs tests against the real Anthropic and Google APIs when credentials are set.

### Changed
- Configuration moved from Twilio and OpenAI settings to `ANTHROPIC_*`, `AT_*`, `GOOGLE_*` and `VOICE_WEBHOOK_SECRET`.
- Negotiations store a provider-neutral `provider_call_id`.

### Removed
- Twilio, the OpenAI Realtime API, and the websocket media-stream path.

### Fixed
- The voice webhook returns 404 instead of a server error when someone guesses a secret containing non-ASCII characters.
- It returns 400 for callbacks it can't decode or parse, and no longer echoes internal parser error text back in that response.
- Any other unhandled error in the call-handling path now ends the call gracefully (a spoken goodbye) instead of a raw 500.
- A failing agent tool no longer passes its internal error details to the model, which could have spoken them to the caller.
- Creating the Google speech client no longer blocks every live call while it loads credentials.
- **The spoken-price guard (`amount_heard`) no longer accepts an unrelated number as a confirmed price:** "twenty three years" or "fifteen months" could previously be misread as KES 23,000 / 15,000. The conversational-thousands heuristic now only fires at the end of an utterance/clause or before an explicit currency word.
- A call session that raises while starting or responding is no longer left registered forever; a provider retry for that call gets a fresh start instead of being misrouted as silence.
- A duplicate/replayed webhook for a call that already ended no longer silently starts a brand-new negotiation, and the hang-up path is now serialized with any turn still in flight for that session.
- A malformed `durationInSeconds` in a call-ended callback (e.g. `1e400`) no longer raises `OverflowError`.
