# Nego

### The negotiation layer for procurement.

**Nego is an AI procurement agent that negotiates with suppliers on behalf of businesses, within purchasing rules defined by the business.**

Built for the **AI Tinkerers Nairobi, "Agents, Everywhere" Global Hackathon**, Nego explores what happens when an AI agent moves beyond chat and into the real-world conversations that happen before a business transaction.

**Set the mandate. Let Nego negotiate. Approve the deal.**

---

## The problem

Procurement software is good at managing suppliers, purchase orders, approvals, invoices, and transactions.

But a significant part of purchasing still happens outside those systems:

**The conversation.**

* "What's your best price?"
* "Can you deliver tomorrow?"
* "We need 500kg, not 300kg."
* "That's above our approved budget."
* "Can you do better if we order regularly?"

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

OFFER REJECTED

Offer exceeds maximum budget.
Maximum: KES 80,000.


AGENT

That's above our approved budget.
Can you improve the price?


SUPPLIER

Best I can do is KES 87,000.


POLICY ENGINE

OFFER REJECTED

Offer still exceeds maximum budget.


AGENT

If we confirm the full 500kg order and continue ordering regularly,
can you meet KES 80,000?


SUPPLIER

Yes. KES 80,000 delivered tomorrow.


POLICY ENGINE

OFFER ACCEPTABLE

Offer satisfies the purchasing mandate.
```

The final offer is presented to the human buyer for approval.

---

## Why Nego is different

Nego is not simply a chatbot that talks to suppliers.

It separates **conversation** from **authority**.

### The AI handles

* Natural language conversation
* Questions to suppliers
* Negotiation
* Counteroffers
* Clarifying requirements
* Gathering commercial terms
* Requesting alternatives

### The policy layer controls

* Maximum budget
* Required quantities
* Required delivery conditions
* Maximum negotiation attempts
* Approval requirements
* Actions the agent is not permitted to take

This allows the AI to be flexible in conversation without giving the model unrestricted purchasing authority.

> **The AI negotiates. The business stays in control.**

---

## The product

### AI negotiation

Nego can conduct a supplier conversation rather than simply recommend what a buyer should say.

### Policy enforcement

Offers are evaluated against deterministic purchasing rules.

### Bounded negotiation

The business defines how far the agent is allowed to negotiate.

### Human approval

The agent does not make the final purchasing commitment without the required human approval.

### Negotiation visibility

The business can see what was requested, what the supplier offered, how the offer was evaluated, and why the final outcome was accepted or rejected.

---

## Safety model

Nego is designed around explicit authority boundaries.

### Agent can do automatically

* Ask for prices
* Ask questions
* Negotiate
* Request better terms
* Request alternatives
* Gather supplier information
* Evaluate offers against purchasing rules

### Human approval required

* Accept a final offer
* Confirm a booking
* Schedule a service
* Make a purchasing commitment

### Agent must never

* Exceed the approved budget
* Ignore mandatory purchasing requirements
* Make an unauthorized purchase
* Reveal sensitive business information
* Negotiate indefinitely
* Commit to terms outside the purchasing mandate

The objective is not to make an AI that says **yes** to everything.

It is to make an AI that knows **what it is allowed to say yes to**.

---

## Architecture

```text
                         BUSINESS
                            |
                            v
                 +----------------------+
                 | Purchasing Mandate   |
                 |----------------------|
                 | Product              |
                 | Quantity             |
                 | Maximum budget       |
                 | Delivery requirements|
                 | Counter limit        |
                 | Approval requirement |
                 +----------+-----------+
                            |
                            v
                 +----------------------+
                 | Nego Agent           |
                 |----------------------|
                 | Conversation         |
                 | Negotiation          |
                 | Reasoning            |
                 | Supplier questions   |
                 +----------+-----------+
                            |
                            v
                 +----------------------+
                 | Policy Engine        |
                 |----------------------|
                 | Budget validation    |
                 | Quantity validation  |
                 | Terms validation     |
                 | Counter validation   |
                 | Authority validation |
                 +----------+-----------+
                            |
                            v
                       SUPPLIER
                            |
                            v
                 +----------------------+
                 | Negotiated Offer     |
                 +----------+-----------+
                            |
                            v
                 +----------------------+
                 | Human Approval       |
                 +----------+-----------+
                            |
                       Approved?
                       /       \
                     YES        NO
                      |          |
                      v          v
                  Commit       Stop
```

The architecture intentionally separates the conversational model from purchasing authority.

The model can negotiate, but the policy layer determines whether an offer is compliant.

---

## Negotiation state

A negotiation can be represented as a bounded state machine:

```text
INITIATED
    |
    v
CONTACTING_SUPPLIER
    |
    v
NEGOTIATING
    |
    +-------------------+
    |                   |
    v                   v
OFFER_RECEIVED      CALL_FAILED
    |
    v
POLICY_EVALUATION
    |
    +-----------+-----------+
    |           |           |
    v           v           v
COMPLIANT    REJECTED    INVALID
    |           |           |
    v           v           v
APPROVAL     COUNTER      CLARIFY
    |           |
    v           v
APPROVED    NEGOTIATING
    |
    v
COMMITTED
```

The negotiation remains bounded by the purchasing mandate and operational limits.

---

## Data model

The core negotiation information can be represented around four concepts.

### Purchasing mandate

```text
Mandate
├── product
├── quantity
├── maximum_budget
├── delivery_requirement
├── required_terms
├── maximum_counters
└── human_approval_required
```

### Supplier offer

```text
SupplierOffer
├── price
├── quantity
├── delivery_terms
├── additional_terms
└── supplier_response
```

### Policy decision

```text
PolicyDecision
├── compliant
├── reason
├── violated_rules
├── remaining_counters
└── approval_required
```

### Negotiation outcome

```text
NegotiationOutcome
├── initial_offer
├── final_offer
├── negotiation_attempts
├── policy_decisions
├── estimated_savings
└── approval_status
```

These structures make it possible to explain why an offer was rejected, why a counteroffer was made, and why a final offer is eligible for approval.

---

## Tool surface

The agent's capabilities are intentionally narrow.

Conceptually, the agent operates through actions such as:

```text
CONTACT_SUPPLIER
ASK_QUESTION
REQUEST_PRICE
MAKE_COUNTEROFFER
REQUEST_ALTERNATIVE
EVALUATE_OFFER
PRESENT_FOR_APPROVAL
STOP_NEGOTIATION
```

The agent does not receive an unrestricted "purchase anything" capability.

The distinction is important:

```text
LLM
 |
 | conversation and reasoning
 v
Agent tools
 |
 v
Policy validation
 |
 v
Allowed action
```

---

## Hard agent rules

The agent operates under explicit constraints:

```text
1. Never exceed the maximum approved budget.

2. Never reduce a mandatory quantity or requirement
   merely to make an offer compliant.

3. Never exceed the configured negotiation limit.

4. Never claim that a purchase has been completed
   before human approval.

5. Never reveal internal business constraints
   beyond what is necessary for negotiation.

6. Stop when the purchasing mandate cannot be satisfied.

7. Escalate final commitment to a human when required.
```

These rules are separate from the model's natural language reasoning.

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

### Initial wedge

The first product wedge is **hospitality and recurring local procurement**, where businesses repeatedly purchase goods or services and negotiate directly with suppliers.

Fresh produce is a particularly useful demonstration scenario because:

* Prices can change frequently
* Orders are recurring
* Quantity and delivery requirements are easy to express
* Negotiation is common
* Savings can be measured directly

The hackathon demonstration uses supplier negotiation to show the underlying capability rather than attempting to build a complete procurement platform.

---

## Why voice?

Many supplier interactions already happen through phone calls.

A procurement agent that only operates inside a web interface still leaves the actual supplier conversation to a human.

Voice allows Nego to enter the environment where the negotiation already happens.

The important capability is not simply making a phone call.

It is:

```text
Business mandate
       |
       v
Supplier conversation
       |
       v
Negotiation
       |
       v
Policy evaluation
       |
       v
Human approval
```

Voice is therefore a communication interface for the negotiation agent, not the product itself.

---

## Built for "Agents, Everywhere"

The AI Tinkerers Nairobi challenge asks:

> **What happens when agents belong somewhere new?**

Nego explores the real-world communication layer of business procurement.

Instead of waiting for a user to open a chatbot and ask what to do, the agent can take a defined mandate into an environment where the actual business interaction happens.

**The agent does not just advise the buyer. It represents the buyer within defined boundaries.**

---

## Technology

The project uses a modular architecture built around:

* Python
* FastAPI
* Anthropic models
* Policy-based decision logic
* Telephony abstraction
* Twilio Voice integration
* Configurable call limits
* Human approval boundaries
* Automated tests

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

The system also uses bounded negotiation rules so that the agent cannot continue countering indefinitely.

---

## Failure handling

A real procurement conversation can fail for reasons unrelated to the negotiation itself.

Nego treats these cases as explicit states rather than assuming every call succeeds.

### Supplier does not answer

The negotiation is stopped or retried according to the configured calling policy.

The agent does not fabricate a supplier response.

### Call drops

The call is treated as incomplete.

The system does not interpret an incomplete conversation as a successful transaction.

### Supplier gives an offer outside authority

The policy engine rejects the offer.

The agent may continue negotiating if the configured counter limit has not been reached.

### Supplier asks an unclear question

The agent can request clarification rather than inventing an answer.

### Supplier cannot satisfy the mandate

The negotiation stops without creating a false successful outcome.

### Telephony is unavailable

The negotiation logic remains separated from the telephony layer so that the core policy and agent components can be tested without requiring a live call.

---

## Human approval is the transaction boundary

Nego intentionally separates negotiation from commitment.

The agent can:

```text
Ask
Negotiate
Counter
Clarify
Evaluate
Recommend
```

But the human controls:

```text
Accept
Reject
Commit
```

This distinction is central to the product.

The goal is not to remove humans from procurement.

The goal is to remove repetitive negotiation work while preserving human control over financial commitments.

---

## Auditability

A useful procurement agent needs to explain its actions.

A negotiation record should make it possible to understand:

```text
What did the business request?
        |
What did the supplier offer?
        |
Why was the offer rejected?
        |
What counteroffer was made?
        |
How many negotiation attempts were used?
        |
What final offer was reached?
        |
Why was it eligible for approval?
        |
Who approved the final commitment?
```

This creates a basis for reviewing negotiation outcomes rather than treating the agent as an opaque black box.

---

## Current status

Nego is a **hackathon prototype / proof of concept**, not a production procurement platform.

The current goal is to demonstrate the core loop:

```text
Purchasing mandate
       |
       v
Supplier conversation
       |
       v
Offer
       |
       v
Policy evaluation
       |
       v
Counteroffer
       |
       v
Compliant offer
       |
       v
Human approval
```

The project deliberately focuses on this narrow vertical slice rather than attempting to build a complete procurement suite.

### Working components

The repository contains the core agent, policy, telephony abstraction, configuration, and test structure required to demonstrate the negotiation workflow.

### Operational controls

Current call-level safeguards include:

```text
MAX_CALLS_PER_DAY=40
CALL_HARD_END_SECONDS=180
```

### Prototype limitations

The current implementation should be treated as an experimental hackathon system.

It is not yet intended to:

* Execute unrestricted purchases
* Replace enterprise procurement systems
* Manage a complete supplier marketplace
* Guarantee supplier availability
* Operate without human oversight for financial commitments

---

## Scope and limitations

The hackathon scope intentionally excludes several larger procurement problems.

### Not currently in scope

* Supplier marketplace discovery
* Large-scale supplier scraping
* Automated supplier onboarding
* Enterprise purchase-order management
* Invoice processing
* Payment execution
* Autonomous purchasing
* Full ERP integration
* Supplier credit management
* Production-grade multi-tenant infrastructure

These may become future integrations, but they are not necessary to demonstrate the core negotiation capability.

---

## What Nego is not

Nego is not:

* A supplier marketplace
* A generic chatbot
* An unrestricted AI purchasing system
* A price-scraping engine
* An ERP replacement
* A payment processor
* A replacement for procurement teams

Nego is focused on one specific problem:

> **Automating the negotiation conversation while keeping purchasing authority with the business.**

---

## Product direction

The long-term product is a procurement negotiation layer that can operate across the communication channels businesses already use.

```text
                    +---------+
                    |  Phone  |
                    +----+----+
                         |
                    +----v----+
                    |         |
+----------+        |  Nego   |       +---------+
| WhatsApp +------->| Agent   |<------|  Email  |
+----------+        |         |       +---------+
                    +----+----+
                         |
               +---------+---------+
               |                   |
        +------v------+     +------v------+
        | Supplier    |     | Supplier    |
        | Portals     |     | APIs        |
        +-------------+     +-------------+
```

The long-term vision is not simply an AI that can make phone calls.

It is an **agentic negotiation layer for business procurement**.

---

## Roadmap

### P0: Hackathon prototype

* Demonstrate mandate-driven negotiation
* Demonstrate supplier conversation
* Demonstrate deterministic policy enforcement
* Demonstrate bounded counteroffers
* Demonstrate human approval
* Demonstrate operational call limits

### P1: Pilot

* Business purchasing mandates
* Supplier profiles
* Negotiation history
* Approval workflows
* Negotiation analytics
* Multi-channel supplier communication

### P2: Product

* Multi-tenant procurement workspaces
* Procurement system integrations
* Supplier APIs
* Purchase-order integration
* Audit and reporting
* Role-based access control
* Production observability
* Enterprise security controls

---

## Business model

Nego is designed as a **B2B SaaS product**.

The business pays for the service because Nego performs procurement work on the buyer's behalf.

The product direction is subscription-based with usage controls for negotiation activity.

The buyer remains the customer.

Nego does not need to take commissions from suppliers to create value.

The core value proposition is straightforward:

```text
Business pays Nego
        |
        v
Nego negotiates
        |
        v
Supplier terms improve
        |
        v
Business saves money and employee time
```

The exact commercial model will be validated during pilot deployments rather than assumed at the hackathon stage.

---

## Repository structure

The repository is organized around the separation between the agent, policy logic, communication layer, and tests.

```text
transaction-agent/
|
├── app/
│   ├── agent/
│   ├── policy/
│   ├── telephony/
│   ├── speech/
│   ├── config.py
│   └── ...
|
├── tests/
│   ├── ...
|
├── README.md
├── requirements.txt
├── .env.example
└── ...
```

The exact module structure may evolve as the prototype develops.

---

## Setup

### Requirements

* Python 3.10+
* pip
* Git
* A configured Anthropic API key for model-powered negotiation
* Twilio credentials for live telephony functionality

Clone the repository:

```bash
git clone https://github.com/SuperiorKe/transaction-agent.git
cd transaction-agent
```

Create a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Create the local environment file:

```bash
cp .env.example .env
```

Add the required credentials and configuration values to `.env`.

**Never commit `.env` to the repository.**

---

## Configuration

Operational configuration is controlled through environment variables.

Important safeguards include:

```text
MAX_CALLS_PER_DAY=40
CALL_HARD_END_SECONDS=180
```

Other environment variables control model access, telephony credentials, and application configuration.

The required values are documented in `.env.example`.

---

## Running the tests

With the virtual environment activated:

```bash
pytest
```

For more detailed output:

```bash
pytest -v
```

The test suite is intended to verify core negotiation behavior without requiring every test to make a live external call.

---

## Running the application

Start the FastAPI application using the configured application entry point:

```bash
uvicorn app.main:app --reload
uvicorn app.main:app --reload
```

When running live telephony functionality, the application must also be reachable by the configured Twilio webhook endpoint.

---

## Telephony

Nego uses a telephony abstraction so that communication infrastructure remains separate from the negotiation logic.

The current live voice integration uses **Twilio Voice**.

The important separation is:

```text
Negotiation logic
       |
       v
Telephony abstraction
       |
       v
Communication provider
```

This allows the core agent and policy system to be tested independently of a particular voice provider.

Live telephony remains subject to the configured operational limits.

---

## Security considerations

This project is a hackathon prototype and should not be treated as production-ready financial infrastructure.

A production deployment would require additional controls around:

* API authentication
* Authorization
* Secret management
* Tenant isolation
* Encryption
* Audit logs
* Personally identifiable information
* Supplier data
* Financial transaction controls
* Rate limiting
* Abuse prevention
* Human approval workflows

API keys and service credentials should never be committed to the repository.

---

## Design principles

Nego is built around a small number of principles.

### 1. Conversation is not authority

The language model can negotiate without becoming the final purchasing authority.

### 2. Rules should be deterministic

Important commercial constraints should not depend entirely on model judgment.

### 3. Negotiation should be bounded

The agent should know when it must stop.

### 4. Commitment requires approval

Financial commitments should remain under explicit business control.

### 5. Failure should be explicit

A failed call or unsuccessful negotiation should not be represented as a successful transaction.

### 6. The agent should be useful where the work happens

The goal is to put the agent into real business communication workflows rather than forcing every interaction into a chat interface.

---

## Hackathon

Built for:

**AI Tinkerers Nairobi, Agents, Everywhere: Bots, Channels, & More**

**12 September 2026**

The challenge asks builders to explore what happens when agents move into new environments, tools, channels, and workflows.

Nego applies that idea to procurement.

Instead of building another chatbot that tells a procurement employee what to say, Nego attempts to represent the business during the supplier negotiation itself.

> **No slides. Demo the thing.**

---

## Core idea

```text
SET THE MANDATE
       |
       v
LET NEGO NEGOTIATE
       |
       v
EVALUATE THE OFFER
       |
       v
HUMAN APPROVAL
       |
       v
COMMIT THE DEAL
```

### Nego

**The negotiation layer for procurement.**

The AI negotiates.

The policy layer enforces the boundaries.

The business makes the final decision.
