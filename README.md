# Transaction Agent

An AI agent that **places a real phone call on your behalf, negotiates within limits you set,
and asks before it commits you to anything.**

Built for the AI Tinkerers Nairobi "Agents, Everywhere" hackathon (12 Sept 2026) as a four-hour
vertical slice. Demo case: booking a photographer in Nairobi.

## What it does

You give the agent an objective and bounded authority — not a blank check:

> "Photographer for Monday in Nairobi. Max KES 20,000. Negotiate twice; never agree above
> KES 20,000 without my approval."

The agent then:

1. Picks a seeded provider and **places a real outbound voice call** (Africa's Talking).
2. Has a turn-based spoken conversation: it records the provider's reply, transcribes it
   (Google Speech-to-Text), reasons about it (Claude), and speaks back.
3. Negotiates the price using a small, fixed set of tools — it can propose a counteroffer or ask
   a clarifying question, but it cannot invent numbers or agree to anything itself.
4. Stops at the line you drew. If the provider won't come down to your budget, asks for a
   deposit, or brings up terms outside its authority, the agent **does not agree** — it ends the
   call and returns a recommendation for you to approve or decline.
5. Writes every decision to an audit trail, so you can see exactly why it did what it did.

The core idea the whole build enforces: **the LLM handles language, deterministic code enforces
authority.** The budget cap, the attempt limit, and every accept/escalate/stop decision are
computed in plain Python (`app/policy.py`), not decided by a prompt. The model never gets a tool
that lets it confirm a booking, ask for a deposit, or speak a price it wasn't given.

## Who this is for

- **Hackathon judges / other builders** evaluating the submission — see [Current status](#current-status)
  for exactly what's real right now vs. still being wired up live at the event.
- **Me, later** — this README plus `CLAUDE.md` (repo conventions, spec pointers, build order) is
  the fast way back into this project after time away.
- **Anyone extending it** to a different service category or negotiation policy — the
  provider/policy/orchestration layers are meant to be swappable.

This is a hackathon prototype, not a product. It does not do payments, provider discovery, or
support more than one service category — see [Scope](#scope--limitations).

## Current status

This is being finished live at the event, so treat this section as the source of truth over any
stale claim elsewhere.

**Working today, offline-tested:**
- Deterministic policy engine: accept/counter/clarify/escalate/stop decisions, counteroffer math,
  and the final recommendation text (`app/policy.py`).
- Transaction/negotiation state machine with enforced legal transitions (`app/states.py`).
- Real outbound calling through Africa's Talking Voice, behind a `TelephonyProvider` interface
  (`app/telephony/africastalking.py`), with a webhook-protected turn loop
  (`app/routes/voice.py`, `app/calls.py`).
- Speech-to-text via Google Cloud Speech-to-Text v2 (`app/speech/google.py`).
- The negotiation conversation itself, running on the Anthropic Messages API through a narrow
  tool surface (`app/agent/`, `app/conversation.py`).
- A full offline test suite (`uv run pytest`) using fakes for all three vendors — no phone
  number, AT credentials, or API key needed to run it.
- Data model and audit trail (`app/models.py`, `app/audit.py`).

**Not built yet — today's remaining work:**
- The `POST /transactions` REST API that creates a transaction, kicks off a call, and exposes
  `approve`/`decline` (issue #6). Right now the negotiation path is reachable via the voice
  webhook and the call coordinator, but nothing yet persists a `Transaction` row or drives the
  full state machine end to end from an HTTP request.
- The owner UI (`web/`) — a minimal screen to create a transaction and approve/decline the
  result. `app/main.py` already serves `web/dist` if it exists; it doesn't yet.
- Wiring `CALL_HARD_END_SECONDS` as an actual circuit breaker on call length (tracked in
  `TODOS.md`).

Check `TODOS.md` and the issues on the private planning repo
(`SuperiorKe/transaction-agent`, epic #10 + issues #1–#9) for the exact remaining list.

## Architecture

```
Owner UI  →  FastAPI  →  agent orchestrator + policy engine  →  telephony (turn-based)
  (React)     (app/)      (app/agent, app/policy.py)             recording → speech-to-text
                                                                  → LLM → text-to-speech
                                                                  → provider
                                                     ↓
                                        outcome evaluator → user approval → confirmation
```

| Layer | Choice | Why |
|---|---|---|
| Backend | FastAPI, Python 3.12, SQLAlchemy 2 | one process, easy to demo |
| State | SQLite, tables created on startup, no migrations | delete the DB file after a schema change |
| Telephony | Africa's Talking Voice, behind `TelephonyProvider` | turn-based `<Record>`/`<Say>` XML over a webhook; AT's sandbox doesn't work, so a real call needs a live/test number |
| Speech-to-text | Google Cloud Speech-to-Text v2 (`chirp_3`, `eu`) | AT has no built-in recognition; `en-KE` isn't supported by v2 so `en-GB` is the default |
| Reasoning | Anthropic Messages API, behind `LLMProvider` | model set by `ANTHROPIC_MODEL` |
| UI | Vite + React + TS in `web/`, built to `web/dist`, served by FastAPI | not built yet |
| Workflow | Plain asyncio tasks | no queue/worker infra for a 4-hour build |

**Vendor isolation is enforced by a test, not just convention** (`tests/test_architecture.py`):
`app/agent`, `app/conversation.py`, `app/calls.py`, `app/policy.py`, and `app/numbers.py` may
never import a telephony SDK or a model SDK directly. All vendor code lives in exactly three
files: `app/telephony/africastalking.py`, `app/speech/google.py`, `app/llm/anthropic.py`. Every
offline test runs against `FakeTelephonyProvider`, `ScriptedLLMProvider`, and `FakeSpeechToText`
instead.

**Tool surface given to the model** (narrow, on purpose): `find_provider`,
`get_transaction_policy`, `start_call`, `record_offer`, `request_user_approval`,
`confirm_booking`, `end_call`. It never gets raw DB or HTTP access. Every accept/escalate/stop
decision is recomputed in `app/policy.py` after the model proposes an action — a disallowed
action is rejected in code even if the model asks for it.

**Hard agent rules** (enforced, not just prompted): identify as an AI when asked · never invent a
price, availability, or agreement · never claim a confirmation before one exists · never guess an
unclear spoken number, ask again instead · a changed price is always a new offer, re-evaluated
from scratch.

### Data model

`providers`, `transactions`, `negotiations`, `offers`, `recommendations`, `approvals`,
`audit_events` — see `app/models.py`. Providers are seeded from `.env`; there's no discovery.

### State machine

```
CREATED → PROVIDER_SELECTED → CALLING → NEGOTIATING
NEGOTIATING → AGREED_WITHIN_POLICY   → RESULT_READY
NEGOTIATING → OUTSIDE_AUTHORITY      → AWAITING_APPROVAL
NEGOTIATING → UNAVAILABLE            → NEXT_PROVIDER | FAILED
RESULT_READY / AWAITING_APPROVAL → APPROVED → CONFIRMING → CONFIRMED
RESULT_READY / AWAITING_APPROVAL → DECLINED → CLOSED
```

Every transition is checked against an explicit allow-list (`app/states.py::ALLOWED_TRANSITIONS`)
and written to `audit_events`; anything not on the list raises instead of silently happening.

## Setup

Toolchain is pinned in `mise.toml` — Python 3.12.14, `uv`, `cloudflared`. System Python is not
used.

```bash
# 1. Toolchain
mise install
# if your shell hasn't picked up mise, prefix every command below with: mise exec --

# 2. Python dependencies
uv sync

# 3. Configuration
cp .env.example .env
# fill in at least: two provider name/phone pairs (E.164 Kenyan, +254...), and — for real
# calls/LLM reasoning — ANTHROPIC_API_KEY, AT_USERNAME/AT_API_KEY/AT_VOICE_NUMBER,
# GOOGLE_APPLICATION_CREDENTIALS, VOICE_WEBHOOK_SECRET. Never commit .env.

# 4. Seed the two providers from .env
uv run python -m app.seed

# 5. Run the API
uv run uvicorn app.main:app --reload --port 8000
```

To let Africa's Talking reach your local webhook, open a tunnel and put its URL in
`WEBHOOK_BASE_URL`:

```bash
cloudflared tunnel --url http://localhost:8000
```

The voice callback then lives at `$WEBHOOK_BASE_URL/webhooks/voice/$VOICE_WEBHOOK_SECRET` — the
secret in the path is the only auth, since AT doesn't sign requests. Everything else that arrives
through the tunnel gets a 403 (`app/middleware.py`): owner routes are local-only by design.

Sanity-check your Anthropic key/model before relying on it live:

```bash
uv run python -m app.llm.anthropic
```

## Running the tests

```bash
uv run pytest                                    # offline, all vendors faked — no keys needed
uv run pytest tests/test_states.py::test_terminal_states_have_no_outgoing_transitions -q
uv run ruff check . && uv run ruff format --check .
uv run pytest -m live                            # hits the real Anthropic + Google APIs
```

Tests use an in-memory SQLite engine (`tests/conftest.py`) and never touch `transaction_agent.db`.
Fake phone numbers in tests use `+2541…` (not `+2547…`) because the pre-publish secret scan greps
for real Kenyan numbers.

## Scope & limitations

**Deliberately not built** (see `04_Hackathon_Build_and_Demo_Plan`): a marketplace, payments,
a universal provider scraper/discovery, a multi-agent swarm, a complex dashboard, production auth
or billing, or more than one service category. Photography in Nairobi is the demo case, not the
product boundary — see the roadmap below.

**Known failure handling:**
- No answer → provider marked unavailable, tries the next seeded provider if one remains.
- Dropped call → one retry, or the partial result is surfaced as-is.
- A question outside the agent's authority → it states the limitation and escalates rather than
  guessing.

**Demo fallback:** if live telephony is unavailable at demo time, a rehearsal recording is used
instead — always labelled on screen, never presented as a live call.

## Roadmap (beyond this hackathon prototype)

P0 (this build) proves the mechanism for one category with two seeded providers. Later phases
(from `01_Transaction_Agent_Product_Spec`) widen it: provider discovery instead of seeding,
multiple service categories, payment execution after approval, and a real multi-user
dashboard/auth layer. None of that is in scope here — the point of P0 is the negotiation loop and
the authority boundary around it, not the surrounding product.

## Repo map

```
app/
  agent/          tool-calling negotiation engine (provider-neutral)
  llm/            LLMProvider interface + Anthropic adapter (only vendor SDK import allowed here)
  telephony/      TelephonyProvider interface + Africa's Talking adapter
  speech/         SpeechToText interface + Google adapter
  routes/         HTTP routes (voice webhook; transaction API is issue #6, pending)
  policy.py       deterministic offer evaluation — no I/O, no clock, pure functions
  states.py       transaction state machine + allowed transitions
  calls.py        connects telephony callbacks to one conversation agent per call
  conversation.py turn-based conversation orchestration
  models.py       SQLAlchemy models (providers, transactions, negotiations, offers, …)
  audit.py        append-only audit trail
  config.py       Settings, mirrors .env.example field-for-field
  seed.py         seeds the two providers from .env
  main.py         FastAPI app, owner-route lockdown, serves web/dist if built
web/              owner UI (Vite + React + TS) — not built yet
tests/            offline test suite; fakes for every vendor; test_architecture.py enforces
                  vendor isolation
CLAUDE.md         conventions, spec pointers, and the canonical demo scenario for this repo
TODOS.md          follow-ups from reviews, beyond the GitHub issue backlog
```

## Where the spec lives

The authoritative build spec is GitHub issues on the private repo
`SuperiorKe/transaction-agent`: **epic #10** holds the shared contracts (DB schema, state
machine, policy rules, API shapes, agent tools, env vars); **issues #1–#9** are the build order.
Where the original `.docx` doc pack and the issues disagree, the issues win — the epic lists
every deliberate deviation. See `CLAUDE.md` for how to read those docs if you have access to them.
