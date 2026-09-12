# Changelog

All notable changes to this project are documented here.
Versions use the 4-digit `MAJOR.MINOR.PATCH.MICRO` format from `VERSION`.

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
