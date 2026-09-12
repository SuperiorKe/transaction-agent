# Transaction Agent

An AI procurement agent that **negotiates with suppliers on behalf of businesses — by phone — while staying within purchasing rules set by the business.**

Transaction Agent turns a business's purchasing mandate into a bounded AI agent:

> "We need 500kg of produce this week. Maximum KES 80,000. Stay within budget, negotiate up to three times, and don't commit without approval."

The agent contacts a supplier, conducts the conversation, evaluates every offer against the purchasing policy, and returns the best compliant outcome for a human to approve or decline.

**The agent negotiates. The business stays in control.**

Built for the AI Tinkerers Nairobi **"Agents, Everywhere"** hackathon (12 Sept 2026) as a focused vertical slice.

---

## The problem

Many businesses in Kenya still negotiate with suppliers through **phone calls and WhatsApp**, not APIs or procurement portals.

A procurement officer may repeatedly have to:

* call suppliers for prices;
* negotiate discounts;
* clarify quantities and terms;
* compare counteroffers;
* make sure the agreed price stays within an approved budget;
* document what was agreed.

Traditional procurement software is good at managing structured workflows.

It is much less useful when the supplier's interface is simply:

**a person answering a phone.**

Transaction Agent is designed for that layer.

---

## What it does

You give the agent an objective and a bounded purchasing mandate — **not a blank cheque**.

For example:

> "Photographer for Monday in Nairobi. Maximum KES 20,000. Negotiate twice. Never agree above KES 20,000 without my approval."

The agent then:

1. **Selects a known supplier** from the business's supplier list.
2. **Places a real outbound phone call** using Twilio.
3. Handles the supplier's spoken response through the voice pipeline.
4. Uses an LLM to conduct the natural-language conversation.
5. Evaluates offers against deterministic purchasing rules.
6. Makes a counteroffer or asks for clarification when permitted.
7. **Stops when the business's authority boundary is reached.**
8. Returns the resulting offer and recommendation to the business.
9. Records the decisions and state changes in an audit trail.

The critical design principle is:

> **The LLM handles language. Deterministic code enforces authority.**

The model does not decide whether an offer is acceptable simply because a prompt tells it to.

The budget cap, negotiation attempt limit, required terms, escalation behaviour, and accept/counter/stop decisions are evaluated in Python (`app/policy.py`).

---

## Why voice?

Transaction Agent is deliberately built around **voice as a supplier interface**.

In our target market, a supplier does not need to expose an API, integrate with a procurement platform, or install another application.

They can simply answer the phone.

That means the business can keep its existing supplier relationships while delegating the repetitive negotiation layer to an agent.

This is particularly relevant for recurring local procurement such as:

* hospitality and produce;
* laundry services;
* transport and logistics;
* catering;
* construction haulage;
* plant hire;
* events and media services;
* other locally negotiated supplier services.

---

## Who this is for

Transaction Agent is being developed for **Kenyan SMEs and mid-market businesses that repeatedly negotiate with local suppliers.**

The initial product thesis focuses on businesses where:

* supplier relationships already exist;
* purchasing happens repeatedly;
* prices or terms are negotiated frequently;
* suppliers primarily communicate through phone or messaging;
* employees spend meaningful time handling repetitive negotiations;
* purchases have clear approval limits.

### Initial wedge

The first target category is **recurring hospitality procurement**, particularly produce.

The reasoning is simple:

* purchases happen frequently;
* prices can change;
* supplier relationships already exist;
* negotiations are relatively understandable;
* the financial risk of an individual failed negotiation is manageable;
* successful negotiations can produce measurable savings.

Other categories can be added later.

---

## The business model

Transaction Agent is a **B2B SaaS product**.

The **business buying the software pays for it**.

Suppliers do not pay to influence negotiations.

The product is intended to use a subscription model with an included allowance of negotiations and metered usage beyond that allowance.

The commercial unit is **the negotiation**, rather than raw call minutes, because businesses can understand and budget for negotiations more easily than telecommunications usage.

Pricing and unit economics are still being validated.

---

## Why the business pays

The product is intended to create value in three places:

### 1. Money saved

The agent can negotiate within the purchasing mandate rather than simply accepting the supplier's first quote.

### 2. Employee time saved

Procurement and operations staff can delegate repetitive supplier conversations while retaining approval authority.

### 3. Purchasing control

Every negotiation is constrained by explicit rules and recorded in an audit trail.

The goal is not to replace procurement teams.

It is to give them an **automated negotiation layer**.

---

## Human approval is the transaction boundary

Transaction Agent does not treat successful negotiation as automatic authorization to purchase.

The agent can negotiate within its delegated authority, but the business remains responsible for the final commitment.

Conceptually:

```text
Business
   │
   │ purchasing mandate
   ▼
Transaction Agent
   │
   │ phone negotiation
   ▼
Supplier
   │
   │ offers / counteroffers
   ▼
Policy Engine
   │
   ├── within authority → continue
   ├── counter allowed → negotiate
   ├── outside authority → stop / escalate
   └── acceptable outcome → recommendation
                              │
                              ▼
                       Human approval
                              │
                         approve / decline
```

This distinction is fundamental to the architecture.

**Negotiation can be delegated. Final purchasing authority does not have to be.**

---

## Deterministic policy enforcement

The core safety mechanism is deliberately outside the LLM.

For example, a business may define:

* maximum budget;
* maximum negotiation attempts;
* required quantity;
* required service terms;
* escalation conditions;
* whether final acceptance requires approval.

The LLM can propose an action.

The policy engine decides whether that action is actually allowed.

```text
LLM proposes:

"Accept KES 85,000"

        ↓

Policy engine:

Maximum = KES 80,000

        ↓

REJECTED
```

The model cannot override the result by changing its wording.

This is the central technical principle of Transaction Agent:

> **Natural language is handled by the model. Authority is enforced by code.**

---

## Auditability

Every important transaction decision is recorded.

The data model includes:

* `providers`
* `transactions`
* `negotiations`
* `offers`
* `recommendations`
* `approvals`
* `audit_events`

For a business, the audit trail is more than debugging information.

It provides a record of:

* what authority was delegated;
* what the supplier offered;
* what the agent proposed;
* why an offer was accepted or rejected;
* when the agent stopped;
* whether human approval was required.

This is important because **delegating purchasing conversations to software requires evidence that the software respected the mandate.**

---

## Current status

This is a hackathon prototype being developed as a focused vertical slice.

### Working today, offline-tested

* Deterministic policy engine: accept/counter/clarify/escalate/stop decisions, counteroffer math, and recommendation generation (`app/policy.py`).
* Transaction/negotiation state machine with enforced legal transitions (`app/states.py`).
* Real outbound calling through **Twilio Voice** behind a `TelephonyProvider` interface.
* Voice webhook and turn-based call coordination (`app/routes/voice.py`, `app/calls.py`).
* Voice/speech processing through the current Twilio-based voice pipeline.
* Negotiation conversation through the Anthropic Messages API with a deliberately narrow tool surface (`app/agent/`, `app/conversation.py`).
* Offline test suite using fakes for external vendors.
* SQLAlchemy data model and append-only audit trail.

### Operational safeguards

The prototype also defines operational limits for live calling:

* `MAX_CALLS_PER_DAY` limits daily call volume.
* `CALL_HARD_END_SECONDS` provides a hard upper bound on call duration.

The current configuration uses a **180-second hard call ceiling**, which puts a concrete upper bound on the telephony exposure of a single negotiation.

### Still being wired

* `POST /transactions` REST API for creating transactions and exposing approval/decline actions.
* Owner UI (`web/`) for creating transactions and reviewing outcomes.
* Full end-to-end persistence and orchestration from transaction creation through negotiation and approval.

Check `TODOS.md` and the GitHub issues for the exact remaining implementation work.

---

## Architecture

```text
Business / Owner UI
        │
        ▼
     FastAPI
        │
        ▼
Transaction Orchestrator
        │
        ├──────────────► Policy Engine
        │                 app/policy.py
        │
        ▼
 Negotiation Agent
        │
        ▼
   Twilio Voice
        │
        ▼
     Supplier
        │
        │ spoken response
        ▼
   Voice Pipeline
        │
        ▼
      LLM
   Anthropic
        │
        ▼
 Policy Evaluation
        │
        ├── continue negotiating
        ├── counter
        ├── clarify
        ├── escalate
        └── stop
        │
        ▼
 Recommendation
        │
        ▼
 Human Approval
```

### Technology

| Layer                 | Choice                         | Purpose                                     |
| --------------------- | ------------------------------ | ------------------------------------------- |
| Backend               | FastAPI + Python 3.12          | Application/API layer                       |
| State                 | SQLite + SQLAlchemy 2          | Transaction and audit persistence           |
| Telephony             | **Twilio Voice**               | Real phone calls                            |
| Speech/voice pipeline | **Current Twilio voice stack** | Handle the live voice interaction           |
| Reasoning             | Anthropic Messages API         | Natural-language negotiation                |
| UI                    | Vite + React + TypeScript      | Owner interface                             |
| Workflow              | Python asyncio                 | Lightweight orchestration for the hackathon |

---

## Vendor isolation

External vendors are isolated behind interfaces.

`app/agent`, `app/conversation.py`, `app/calls.py`, `app/policy.py`, and `app/numbers.py` do not import telephony or model SDKs directly.

Vendor-specific integrations are kept behind their respective adapters.

The telephony integration is:

```text
app/telephony/
```

and the Twilio implementation sits behind the `TelephonyProvider` interface.

Offline tests use fake providers so the negotiation and policy logic can be tested without making real calls or consuming external API credits.

The architecture test (`tests/test_architecture.py`) enforces this separation.

---

## Tool surface

The model receives a deliberately narrow set of tools:

```text
find_provider
get_transaction_policy
start_call
record_offer
request_user_approval
confirm_booking
end_call
```

It does not receive arbitrary database, HTTP, or infrastructure access.

Most importantly, the model cannot simply decide that a transaction is acceptable.

Actions are re-evaluated by deterministic code before they are allowed to proceed.

---

## Hard agent rules

The agent is designed to:

* identify itself as an AI when asked;
* never invent a price, availability, or agreement;
* never claim confirmation before confirmation exists;
* never guess an unclear spoken number;
* ask the supplier to repeat unclear information;
* treat every changed price as a new offer;
* re-evaluate every offer against the current policy;
* never exceed the delegated purchasing authority;
* stop or escalate when an action is outside its authority.

---

## Data model

The core entities are:

```text
providers
transactions
negotiations
offers
recommendations
approvals
audit_events
```

Providers are currently seeded from `.env`.

**Provider discovery is intentionally outside the current hackathon scope.**

The product assumes that a business already has suppliers it is permitted to contact.

---

## State machine

```text
CREATED
   ↓
PROVIDER_SELECTED
   ↓
CALLING
   ↓
NEGOTIATING
   ├──► AGREED_WITHIN_POLICY → RESULT_READY
   ├──► OUTSIDE_AUTHORITY    → AWAITING_APPROVAL
   ├──► UNAVAILABLE          → NEXT_PROVIDER / FAILED
   └──► FAILED

RESULT_READY / AWAITING_APPROVAL
   ├──► APPROVED → CONFIRMING → CONFIRMED
   └──► DECLINED → CLOSED
```

Every transition is checked against an explicit allow-list in:

```text
app/states.py::ALLOWED_TRANSITIONS
```

Invalid transitions raise an error instead of silently occurring.

---

## Setup

Toolchain is pinned in `mise.toml`.

```bash
# 1. Install the pinned toolchain
mise install

# If your shell has not picked up mise:
# prefix commands with:
# mise exec --

# 2. Install Python dependencies
uv sync

# 3. Configure environment
cp .env.example .env
```

For a real call, configure the required credentials and provider information in `.env`:

```text
ANTHROPIC_API_KEY
ANTHROPIC_MODEL
TWILIO_ACCOUNT_SID
TWILIO_AUTH_TOKEN
TWILIO_PHONE_NUMBER
VOICE_WEBHOOK_SECRET
WEBHOOK_BASE_URL
```

Never commit `.env`.

Seed the configured providers:

```bash
uv run python -m app.seed
```

Run the API:

```bash
uv run uvicorn app.main:app --reload --port 8000
```

To expose the local webhook to Twilio:

```bash
cloudflared tunnel --url http://localhost:8000
```

Configure the Twilio Voice webhook to point to the appropriate voice callback under:

```text
$WEBHOOK_BASE_URL
```

---

## Running the tests

```bash
# Offline test suite
uv run pytest

# Specific state-machine test
uv run pytest tests/test_states.py::test_terminal_states_have_no_outgoing_transitions -q

# Lint + formatting
uv run ruff check .
uv run ruff format --check .

# Tests that hit real external APIs
uv run pytest -m live
```

The default test suite does not require:

* a Twilio phone number;
* Twilio credentials;
* an Anthropic API key;
* external voice infrastructure.

Tests use an in-memory SQLite database and fake external providers.

---

## Scope & limitations

Transaction Agent is intentionally **not** a full procurement platform yet.

### Not in the current hackathon scope

* supplier marketplace;
* universal supplier discovery;
* web scraping;
* payments;
* automatic purchasing without approval;
* multi-agent swarm architecture;
* complex analytics dashboard;
* production authentication;
* billing;
* enterprise SSO;
* multiple procurement categories;
* large-scale worker/queue infrastructure.

The current prototype uses **known/seeded suppliers**.

That is deliberate.

A business already has suppliers. The product's initial job is to automate the negotiation with those suppliers, not to build another marketplace.

---

## Known failure handling

### Supplier does not answer

The provider is marked unavailable and another seeded provider may be attempted if one exists.

### Call drops

The system can retry within the configured retry policy or surface the partial result.

### Supplier gives an offer outside authority

The agent does not accept it.

The transaction is stopped or escalated according to policy.

### Supplier asks an unclear question

The agent asks for clarification rather than inventing an answer.

### Live telephony is unavailable

A rehearsal recording can be used for the hackathon demonstration.

It is explicitly labelled as a rehearsal and never presented as a live call.

---

## Product direction

The long-term product thesis is:

> **The negotiation layer for procurement.**

Existing procurement systems manage structured purchasing workflows.

Transaction Agent is intended to handle the messy human interaction that happens before a transaction:

**the phone calls, questions, counteroffers and negotiations.**

The first wedge is narrow:

```text
Kenyan business
      ↓
Recurring local procurement
      ↓
Known suppliers
      ↓
Phone negotiation
      ↓
AI agent
      ↓
Deterministic purchasing policy
      ↓
Human approval
```

From there, the product can expand into additional supplier categories and procurement workflows.

The current priority is **not** building every procurement feature.

It is proving that a business can safely delegate a real supplier negotiation to an AI agent without giving the agent unrestricted purchasing authority.

---

## Roadmap

### P0 — Hackathon prototype

Prove the negotiation mechanism:

* one procurement category;
* seeded suppliers;
* real outbound voice;
* bounded negotiation;
* deterministic policy enforcement;
* audit trail;
* human approval boundary.

### P1 — Pilot

Validate the product with real businesses:

* one repeating procurement category;
* real supplier relationships;
* negotiation success rate;
* supplier willingness to talk to an AI agent;
* average negotiation duration;
* cost per successful negotiation;
* measurable savings/time saved.

### P2 — Product

Potential expansion:

* multiple supplier categories;
* supplier list ingestion;
* approval workflows;
* business accounts;
* audit exports;
* usage metering;
* billing;
* stronger authentication;
* supplier discovery where appropriate;
* post-approval transaction execution.

The roadmap should follow evidence from pilots rather than building a large procurement platform before the negotiation loop is proven.

---

## Repo map

```text
app/
  agent/          tool-calling negotiation engine
  llm/            LLMProvider interface + Anthropic adapter
  telephony/      TelephonyProvider interface + Twilio adapter
  routes/         HTTP routes, including voice webhook
  policy.py       deterministic offer evaluation — no I/O, pure policy logic
  states.py       transaction state machine + allowed transitions
  calls.py        connects telephony callbacks to conversations
  conversation.py turn-based conversation orchestration
  models.py       SQLAlchemy models
  audit.py        append-only audit trail
  config.py       application settings
  seed.py         seeds configured providers
  main.py         FastAPI application

web/
  owner UI — Vite + React + TypeScript

tests/
  offline tests and vendor fakes
  test_architecture.py enforces vendor isolation

CLAUDE.md
  repository conventions, specification pointers,
  and canonical demo scenario

TODOS.md
  follow-ups and remaining implementation work
```

---

## Where the specification lives

The authoritative build specification is maintained in the private GitHub planning repository:

```text
SuperiorKe/transaction-agent
```

**Epic #10** contains the shared contracts:

* database schema;
* state machine;
* policy rules;
* API shapes;
* agent tools;
* environment variables.

**Issues #1–#9** define the implementation order.

Where the original document pack and the GitHub issues disagree, the issues take precedence.

See `CLAUDE.md` for repository conventions and specification references.

---

## The core idea

Transaction Agent is not trying to make an AI that can **do anything**.

It is trying to make an AI that can **do one consequential thing safely**:

> **Negotiate with a supplier on behalf of a business without exceeding the authority that business gave it.**

**The agent negotiates.
The policy enforces.
The audit trail records.
The human remains in control.**
