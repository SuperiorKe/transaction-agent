# Nego

### The negotiation layer for procurement.

**Nego is an AI procurement agent that negotiates with suppliers on behalf of businesses — within purchasing rules defined by the business.**

Built for the **AI Tinkerers Nairobi — “Agents, Everywhere” Global Hackathon**, Nego explores what happens when an AI agent moves beyond chat and into the real-world conversations that happen before a business transaction.

---

## The problem

Procurement software is good at managing suppliers, purchase orders, approvals, invoices, and transactions.

But a significant part of purchasing still happens outside those systems:

**The conversation.**

* “What's your best price?”
* “Can you deliver tomorrow?”
* “We need 500kg, not 300kg.”
* “That's above our approved budget.”
* “Can you do better if we order regularly?”

For many businesses, these conversations are repetitive, time-consuming, and difficult to automate safely.

Nego is built to handle that negotiation layer.

---

## What Nego does

A business gives Nego a purchasing mandate:

> **We need 500kg of produce. Maximum KES 80,000. Delivery tomorrow. Negotiate up to three times. Do not commit without approval.**

Nego then:

1. Understands the purchasing mandate
2. Contacts the supplier
3. Conducts the negotiation
4. Evaluates each offer against the purchasing rules
5. Rejects offers that violate the mandate
6. Counters within the allowed limits
7. Presents the final compliant offer to a human
8. Waits for approval before the final commitment

### Example

**Business mandate**

| Requirement      | Rule             |
| ---------------- | ---------------- |
| Product          | 500kg vegetables |
| Maximum budget   | KES 80,000       |
| Delivery         | Tomorrow         |
| Maximum counters | 3                |
| Final commitment | Human approval   |

**Negotiation**

```text
AGENT
We're looking for 500kg of mixed vegetables delivered tomorrow.
What can you offer?

SUPPLIER
I can do KES 95,000.

POLICY ENGINE
❌ Offer exceeds maximum budget.
Maximum: KES 80,000.

AGENT
That's above our approved budget.
Can you improve the price?

SUPPLIER
Best I can do is KES 87,000.

POLICY ENGINE
❌ Offer still exceeds maximum budget.

AGENT
If we confirm the full 500kg order and continue ordering regularly,
can you meet KES 80,000?

SUPPLIER
Yes. KES 80,000 delivered tomorrow.

POLICY ENGINE
✅ Offer satisfies the purchasing mandate.
```

The final offer is then presented for human approval.

---

## Why Nego is different

Nego isn't simply a chatbot that talks to suppliers.

It separates **conversation** from **authority**.

### The AI handles

* Natural-language conversation
* Questions to suppliers
* Negotiation
* Counteroffers
* Clarifying requirements
* Gathering commercial terms

### The policy layer controls

* Maximum budget
* Required quantities
* Required delivery conditions
* Maximum negotiation attempts
* Approval requirements
* Actions the agent is not permitted to take

This means the AI can be flexible in conversation without being free to invent its own purchasing authority.

> **The AI negotiates. The business stays in control.**

---

## The product

###  AI negotiation

Nego can conduct a supplier conversation rather than simply recommend what a buyer should say.

###  Policy enforcement

Every offer can be evaluated against deterministic purchasing rules.

###  Bounded negotiation

The business defines how far the agent is allowed to negotiate.

###  Human approval

The agent does not make the final purchasing commitment without the required human approval.

###  Negotiation visibility

The business can see what was requested, what the supplier offered, how the offer was evaluated, and why the final outcome was accepted or rejected.

---

## Safety model

Nego is designed around three levels of authority.

###  Agent can do automatically

* Ask for prices
* Ask questions
* Negotiate
* Request better terms
* Request alternatives
* Gather supplier information

###  Human approval required

* Accept a final offer
* Confirm a booking
* Schedule a service
* Make a purchasing commitment

###  Agent must never

* Exceed the approved budget
* Ignore mandatory purchasing requirements
* Make an unauthorized purchase
* Reveal sensitive business information
* Negotiate indefinitely

The objective isn't to make an AI that says **yes** to everything.

It is to make an AI that knows **what it is allowed to say yes to**.

---

## Architecture

```text
                    BUSINESS
                       │
                       ▼
              ┌─────────────────┐
              │ Purchasing      │
              │ Mandate         │
              └────────┬────────┘
                       │
                       ▼
              ┌─────────────────┐
              │ Nego Agent      │
              │                 │
              │ Conversation    │
              │ Negotiation     │
              │ Reasoning       │
              └────────┬────────┘
                       │
                       ▼
              ┌─────────────────┐
              │ Policy Engine   │
              │                 │
              │ Budget          │
              │ Quantity        │
              │ Terms           │
              │ Counter limit   │
              └────────┬────────┘
                       │
                       ▼
                 SUPPLIER
                       │
                       ▼
              ┌─────────────────┐
              │ Final Offer     │
              └────────┬────────┘
                       │
                       ▼
              ┌─────────────────┐
              │ Human Approval  │
              └─────────────────┘
```

The architecture is intentionally designed so that the conversational model does not become the final authority over a business transaction.

---

## Why procurement?

Nego is initially focused on business purchasing scenarios where supplier negotiation is frequent and still happens through direct human communication.

Potential use cases include:

* Hospitality procurement
* Fresh produce purchasing
* Catering
* Transport
* Construction services
* Equipment and plant hire
* Events and media services
* Recurring local business suppliers

The initial hackathon demonstration uses a supplier negotiation scenario to show the core capability.

---

## Built for “Agents, Everywhere”

The AI Tinkerers Nairobi challenge asks:

> **What happens when agents belong somewhere new?**

Nego explores the **real-world communication layer** of business procurement.

Instead of waiting for a user to open a chatbot and ask what to do, the agent can take a defined mandate into an environment where the actual business interaction happens.

**The agent doesn't just advise the buyer. It represents the buyer within defined boundaries.**

---

## Technology

The project is built around a modular agent architecture with:

* Python
* FastAPI
* Anthropic models
* Policy-based decision logic
* Telephony abstraction
* Twilio Voice integration
* Configurable call limits
* Human approval boundaries

The telephony layer is abstracted so that the negotiation agent and policy logic are not tightly coupled to a single communications provider.

---

## Operational safeguards

The system includes operational controls designed to prevent runaway calls and excessive usage.

```text
MAX_CALLS_PER_DAY=40
CALL_HARD_END_SECONDS=180
```

These provide:

* A daily call-volume ceiling
* A hard maximum duration for an individual call

These limits are separate from the commercial pricing model and exist as operational safety controls.

---

## Current status

Nego is a **hackathon prototype / proof of concept**, not a production procurement platform.

The current goal is to demonstrate the core loop:

```text
Purchasing mandate
       ↓
Supplier conversation
       ↓
Offer
       ↓
Policy evaluation
       ↓
Counteroffer
       ↓
Compliant offer
       ↓
Human approval
```

The project deliberately focuses on this narrow vertical slice rather than attempting to build a complete procurement suite.

---

## What Nego is not

Nego is not:

* A supplier marketplace
* A generic chatbot
* An AI purchasing system with unrestricted authority
* A price-scraping engine
* A replacement for procurement teams

Nego is focused on one specific problem:

> **Automating the negotiation conversation while keeping purchasing authority with the business.**

---

## Future direction

The same negotiation agent could eventually operate across multiple business communication channels:

```text
              ┌── Phone
              │
              ├── WhatsApp
Business ────┼── Email
Mandate       │
              ├── Supplier portals
              │
              └── APIs
```

The long-term vision is not simply an AI that can make phone calls.

It is an **agentic negotiation layer for business procurement**.

---

## Hackathon

Built for:

**AI Tinkerers Nairobi — Agents, Everywhere: Bots, Channels, & More**

**12 September 2026**

The principle behind the build:

> **No slides. Demo the thing.**

---

## Core idea

**Set the mandate.
Let Nego negotiate.
Approve the deal.**

### Nego

**The negotiation layer for procurement.**
