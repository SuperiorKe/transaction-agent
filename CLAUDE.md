# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

Prototype 0 is being built from a spec filed as GitHub issues on the private repo `SuperiorKe/transaction-agent`:
- **Epic #10** holds the shared contracts: DB schema, state machine, policy rules, API shapes, agent tools and env vars.
- **Issues #1–#9** are the build order.

Where the issues and the .docx docs disagree, the issues win. The epic lists every deliberate deviation from the docs.

**Live pivot (12 Sep 2026, mid-build):** Africa's Talking's real-call path didn't work, so telephony moved to **Twilio**. Google Cloud Speech-to-Text was dropped in the same move — a teammate got blocked on Google Cloud account setup, and Twilio's own `<Gather input="speech">` transcribes the caller inline (no separate STT vendor, no service-account JSON). This file's Stack table below reflects the new decision; issue #2 and #5's *titles and original bodies* still say Africa's Talking / Google Cloud — treat this file as current over those until they're edited.

`Transaction_Agent_Hackathon_Document_Pack/` is **read-only**: never modify it. It's gitignored. `pandoc` isn't installed, so read a doc with:

```
unzip -p Transaction_Agent_Hackathon_Document_Pack/<file>.docx word/document.xml | sed -e 's/<\/w:p>/\n/g' -e 's/<[^>]*>//g'
```

- `01_Transaction_Agent_Product_Spec` — the long-term product vision, its boundaries, and the roadmap (P0–P4)
- `02_Prototype_0_Technical_Spec` — **what is being built now**: data model, API, agent tools, state machine, env vars
- `03_Agent_Negotiation_Spec` — the rules the agent must follow, deterministic evaluation, recommendation schema, edge cases
- `04_Hackathon_Build_and_Demo_Plan` — the schedule, the stack and its fallbacks, the "do not build" list, and the README requirements

## Commands

The toolchain is pinned in `mise.toml` (Python 3.12.14, uv, cloudflared). System Python 3.14 isn't used. If the shell hasn't activated mise, prefix commands with `mise exec --`.

| Task | Command |
|---|---|
| Install toolchain | `mise install` |
| Python deps | `uv sync` |
| Seed the 2 providers (from `.env`) | `uv run python -m app.seed` |
| API server | `uv run uvicorn app.main:app --reload --port 8000` |
| Public tunnel for Twilio callbacks | `cloudflared tunnel --url http://localhost:8000`, then put the URL in `WEBHOOK_BASE_URL` |
| Tests (offline; `sim` marker excluded) | `uv run pytest` |
| Single test | `uv run pytest tests/test_states.py::test_terminal_states_have_no_outgoing_transitions -q` |
| Lint / format | `uv run ruff check . && uv run ruff format --check .` |
| Live Anthropic tests (needs `ANTHROPIC_API_KEY`) | `uv run pytest -m live` |
| Check `ANTHROPIC_MODEL` is available to your key | `uv run python -m app.llm.anthropic` |

`grep` in this shell is ugrep, which rejects long bounded regexes. Use `command grep` or Python for those.

At the end of a substantial working session (or whenever you want the signal pulled out of a
long conversation rather than a chronological recap), invoke the `session-extract` skill. It
writes a two-pass extraction to `~/.claude/session-extractions/transaction-agent/` — outside
this repo, so it never collides with another concurrent session's git activity the way an
in-repo extraction file did once already.

Tests use an in-memory SQLite engine (`tests/conftest.py`). Use `TestClient(create_app())` without a `with` block so the lifespan doesn't create the real `transaction_agent.db`. Fake phone numbers in tests use the `+2541…` range, because #9's pre-publish secret check greps for `+2547…`.

## What this is

Transaction Agent acts for a user in a real-world service transaction. The user gives an objective plus bounded authority (budget cap, number of negotiation attempts). The agent places a **real outbound phone call** to a provider, negotiates within that authority, and returns a recommendation. The user then approves or declines. Photography in Nairobi is the demo case, not the product boundary. The build is for the AI Tinkerers Nairobi "Agents, Everywhere" hackathon on 12 September 2026, as a four-hour vertical slice.

Canonical test scenario: "Photographer for Monday in Nairobi. Max KES 20,000. Negotiate twice; never agree above KES 20,000 without my approval." A consenting teammate plays the provider and opens at KES 23,000. Expected result: one bounded counteroffer, then no commitment if the price is still above the cap, and the transaction moves to an approval/recommendation state.

## Architecture principles (these span every component)

- **The LLM does language; deterministic code enforces authority.** Budget caps, attempt limits, and escalation must be checked in application code (Python/TS) that rejects disallowed actions even when the model requests them. The prompt is *not* the enforcement mechanism.
- **Only narrow tools go to the model.** Never give it unrestricted DB or HTTP access. Planned tool surface: `find_provider`, `get_transaction_policy`, `start_call`, `record_offer`, `request_user_approval`, `confirm_booking`, `end_call`.
- **Deterministic offer evaluation** (spec 03 §6):
  - `amount <= hard_budget AND available AND required_terms_present AND attempts < max_attempts` → `MAY_ACCEPT`
  - `amount > hard_budget` → `MUST_ESCALATE`
  - ambiguous terms → `CLARIFY_OR_ESCALATE`
  - attempt limit reached → `STOP_NEGOTIATING`
- **Escalation triggers:** price above cap; any request for payment or a deposit; material cancellation/refund terms; requests for sensitive info; conflicting or incomplete requirements; any commitment beyond tool permissions.
- **Hard agent rules:** identify as an AI assistant when asked. Never invent prices, availability, discounts, or agreements. Never claim confirmation before it exists. Never infer critical numbers from unclear audio (ask again instead). A changed price counts as a new offer and is re-evaluated. "Tell your client I said yes" requires verifying the exact terms first.
- **Every action is written to an audit trail** (`audit_events` table).

## Planned system (Prototype 0)

Flow: Owner UI → API (FastAPI preferred) → agent orchestrator + policy engine → telephony provider (turn-based: `<Gather>` speech recognition → LLM → text-to-speech) → provider → outcome evaluator → user approval → confirmation.

- **Data model:** `providers`, `transactions`, `negotiations`, `offers`, `approvals`, `audit_events` (fields in spec 02 §5). Providers are seeded; discovery is out of scope for P0.
- **API:**
  - `POST /transactions`
  - `GET /transactions/{id}`
  - `POST /transactions/{id}/start`
  - `POST /transactions/{id}/approve`
  - `POST /transactions/{id}/decline`
  - `POST /webhooks/voice`
  - `POST /webhooks/call-result`
- **Transaction state machine:**
  - `CREATED → PROVIDER_SELECTED → CALLING → NEGOTIATING`
  - `NEGOTIATING → AGREED_WITHIN_POLICY → RESULT_READY`
  - `NEGOTIATING → OUTSIDE_AUTHORITY → AWAITING_APPROVAL`
  - `NEGOTIATING → FAILED/UNAVAILABLE → NEXT_PROVIDER | FAILED`
  - `RESULT_READY → APPROVED → CONFIRMING → CONFIRMED`
  - `RESULT_READY → DECLINED → CLOSED`
- **Recommendation output:** provider, availability, final offered price, terms, policy status (`WITHIN_LIMIT` / `REQUIRES_APPROVAL`), recommendation (`ACCEPT` / `ASK_USER` / `DECLINE`), and reason.
- **Failure handling:**
  - no answer → mark unavailable and try the next seeded provider if allowed
  - dropped call → retry once or surface the partial result
  - unsupported question → state the limitation and escalate
- **Env vars:** see `.env.example`, mirrored field for field by `app/config.py`. Provider phone numbers live only in `.env` and are never committed.

## Stack (decided in epic #10; supersedes the doc 04 preferences)

| Layer | Choice |
|---|---|
| Backend | FastAPI on Python 3.12, SQLAlchemy 2 |
| State | SQLite via `DATABASE_URL`; tables created on startup, no migrations (delete the DB file after a schema change) |
| Telephony | **Twilio** Voice behind `TelephonyProvider` (`app/telephony/twilio.py`). Turn-based: callback webhook → `<Gather input="speech">` wrapping `<Say>` → `SpeechResult`/`Confidence` in the next callback → next turn. Reached through a cloudflared quick tunnel. Trial account: destination numbers must be verified under Console → Verified Caller IDs, and a trial announcement plays before connecting unless the account is topped up (remove before the judged demo). Superseded Africa's Talking (`app/telephony/africastalking.py`, kept in the repo, no longer wired into the live path) after its real-call path failed. |
| Speech-to-text | **Twilio's built-in speech recognition** (`<Gather input="speech">`), returned inline in the telephony callback — no separate vendor, no cloud account. Superseded Google Cloud Speech-to-Text v2 (`app/speech/google.py`, `SpeechToText` interface, kept but unused) after account setup blocked a teammate mid-build. If Twilio's recognition proves unreliable on real Kenyan-accented calls, the fallback is Deepgram or AssemblyAI free tier (API-key signup only, no IAM) — not Google Cloud again. |
| Reasoning | Anthropic Messages API behind `LLMProvider` (`app/llm/`). Model from `ANTHROPIC_MODEL` (default `claude-opus-5`, effort `low`, server-side refusal fallbacks). |
| UI | Vite + React + TS in `web/`, built to `web/dist` and served by FastAPI |
| Workflow | Plain asyncio tasks |
| Demo fallback | A rehearsal recording, labelled on screen and never presented as live |

Owner routes are local only. `app/middleware.py` returns 403 for anything arriving through the tunnel (it carries a `cf-connecting-ip` header) except `/webhooks/*`. Voice callbacks go to `/webhooks/voice/{VOICE_WEBHOOK_SECRET}`. Twilio does sign its webhooks (`X-Twilio-Signature`), unlike AT; validating that signature is a stretch goal, the secret-path scheme is the baseline auth for now.

Vendor boundaries are enforced by `tests/test_architecture.py`:
- `app/agent`, `app/conversation.py`, `app/calls.py`, `app/policy.py` and `app/numbers.py` never import telephony or a model SDK.
- Vendor code lives only in `app/telephony/twilio.py` (live), `app/telephony/africastalking.py` (superseded, kept), `app/speech/google.py` (superseded, kept) and `app/llm/anthropic.py`.
- Offline tests use `FakeTelephonyProvider`, `ScriptedLLMProvider` and `FakeSpeechToText`. No test needs a phone number or vendor credentials.

## Build priorities and scope

- Telephony is the biggest risk. Build order: real outbound call → voice↔AI conversation → structured tools → policy checks → persistence → minimal approval UI → hardening.
- **Do not build:** marketplace, payments, universal scraper/discovery, multi-agent swarm, complex dashboard, production auth or billing, multiple service categories.
- The submission needs a public repo and a README covering setup, architecture, scenario, limitations, and roadmap.
