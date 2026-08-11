# The agentic pipeline

The AI brain of the system. A LangGraph state machine of **20 nodes, 5 conditional routers and 3 ways to finish**. Ten nodes call a language model. Ten are plain Python.

Source of truth: `agentic_pipeline/graph.py`.

---

## 1. The design rule

Every node that calls a model returns an **observation**. Every node that can reject, block or cap something is **Python**.

Look at how they alternate:

| Node | Type | What it produces |
|---|---|---|
| `document_triage` | AI | "This looks like a menu, confidence 0.94" |
| `claim_type_classify` | AI | "Probably fire, confidence 0.82" |
| `requirements_check` | Python | **Decides** the claim halts for missing documents |
| `policy_extract` | AI | "The schedule says cover runs to 31 March" |
| `intake` | Python | **Decides** the loss date is outside the policy period |
| `evidence_verify` | Python | **Decides** a photo failed the geofence |
| `vision` | AI | "I see two damaged laptops" |
| `document_extract` | AI | "The invoice lists quantity 1" |
| `valuation_agent` | AI | "Around 45000 rupees each" |
| `valuation_fallback` | Python | **Decides** to zero out anything still unpriced |
| `policy_agent` | AI | "Clause 4.2 seems to cover electronics" |
| `reconciliation_agent` | AI | "This item needs a purchase invoice" |
| `plausibility_check` | Python | **Decides** to reject items on fraud signals |
| `reserve_estimate` | Python | Adds up the money |
| `drafter` | AI | Writes the pack prose |
| `qc_guardian` | AI | "Section 3 cites a clause I cannot find" |
| `send` | Python | **Decides** to ship, flagged if QC never passed |
| `proof_of_intimation` | Python | Hashes the pack into a receipt |

No model output is ever trusted as a decision.

---

## 2. The whole graph

```mermaid
flowchart TB
  S([START])

  subgraph P1["Phase 1: intake and safety gate"]
    direction TB
    t1["document_triage<br/><i>AI</i>"] --> t2["claim_type_classify<br/><i>AI</i>"] --> t3["requirements_check"] --> R1{"documents<br/>all present?"}
    R1 -->|"no"| t4["awaiting_documents"] --> E1([END])
    R1 -->|"yes"| t5["policy_extract<br/><i>AI</i>"] --> t6["intake"] --> R2{"intake<br/>passed?"}
    R2 -->|"no"| t7["intake_rejected"] --> E2([END])
  end

  subgraph P2["Phase 2: evidence and extraction"]
    direction TB
    v1["evidence_verify"] --> v2["vision<br/><i>AI</i>"] --> v3["document_extract<br/><i>AI</i>"]
  end

  subgraph P3["Phase 3: valuation, coverage and audit"]
    direction TB
    a1["valuation_agent<br/><i>AI</i>"] --> R3{"everything<br/>priced?"}
    R3 -->|"no, retry"| a1
    R3 -->|"gave up"| a2["valuation_fallback"] --> a3["policy_agent<br/><i>AI</i>"]
    R3 -->|"yes"| a3
    a3 --> R4{"missing<br/>paperwork?"}
    R4 -->|"yes"| a4["reconciliation_agent<br/><i>AI</i>"] --> a5["plausibility_check"]
    R4 -->|"no"| a5
    a5 --> a6["reserve_estimate"]
  end

  subgraph P4["Phase 4: drafting, QC and dispatch"]
    direction TB
    d1["drafter<br/><i>AI</i>"] --> d2["qc_guardian<br/><i>AI</i>"] --> R5{"QC<br/>passed?"}
    R5 -->|"no, redraft"| d1
    R5 -->|"yes, or out of tries"| d3["send"] --> d4["proof_of_intimation"] --> E3([END])
  end

  S --> P1
  P1 --> P2
  P2 --> P3
  P3 --> P4

  classDef llm fill:#12386b,stroke:#4da3ff,stroke-width:2px,color:#eaf4ff
  classDef det fill:#123d2e,stroke:#4fc38a,stroke-width:2px,color:#e6fff4
  classDef rtr fill:#5c3d13,stroke:#e0a458,stroke-width:2px,color:#fff6e6
  classDef term fill:#5a1220,stroke:#ff7b8a,stroke-width:2px,color:#ffe9ec

  class t1,t2,t5,v2,v3,a1,a3,a4,d1,d2 llm
  class t3,t4,t6,t7,v1,a2,a5,a6,d3,d4 det
  class R1,R2,R3,R4,R5 rtr
  class E1,E2,E3 term
```

Blue is AI. Green is Python. Orange is a routing decision. Red is an exit.

---

## 3. Phase 1: is this claim workable?

This phase sits in front of everything expensive on purpose. A claim that can never be completed should cost two AI calls, not a dozen.

### document_triage
Looks at each upload and reports what it actually appears to be. Somebody uploading a restaurant menu labelled as a policy schedule gets caught here.

The model returns a kind and a confidence. Python then applies `_triage_verdict`:

* The finding only blocks if confidence is at or above `doc_triage_mismatch_confidence` (default 0.80).
* If `doc_gate_mode` is `warn_only`, it never blocks at all, only warns.

### claim_type_classify
Works out what kind of claim this is (fire, flood, burglary, accident) from the description and up to three photos.

### requirements_check
Narrows the insurer's master requirement list down to this claim, then checks it against what was actually uploaded. Contains **no AI**. Detailed in [overview.md](overview.md#5-insurer-rulesets-and-the-letter-of-requirement).

**Exit 1:** anything blocking is missing, the claim stops at `awaiting_documents` with a checklist.

### policy_extract
Reads the uploaded policy schedule and pulls out the period, the sums insured and the clauses.

### intake
The hard gate. Pure Python:

* Is the loss date inside the policy period? A date within `policy_period_boundary_tolerance_days` (default 1 day) of the edge gets a warning, not a rejection.
* Did document triage produce blocking findings?
* Did the gateway produce blocking findings, such as the same file used for two roles?

**Exit 2:** any of these fail and the claim stops at `intake_rejected`, with a pack explaining what was received and what each file looked like.

---

## 4. Phase 2: what was actually damaged?

### evidence_verify
Three deterministic checks per photo:

1. **Hash match.** Rehash the stored file and compare it to the declared SHA 256.
2. **Geofence.** Straight line distance from the insured premises must be within `geofence_radius_m` (default 150 metres).
3. **Time window.** The capture time must fall between `flood_window_hours_before` (6 hours before the event) and `flood_window_hours_after` (240 hours after).

A photo failing any of these is marked unverified with the reason recorded. It is not silently dropped.

### vision
The item extractor. Runs a vision model over each unprocessed photo and proposes line items with name, category, quantity and any visible serial number.

If the claimant already confirmed items on the preview screen, the gateway marks that evidence as processed and this node skips it. Human confirmation beats a second model run.

### document_extract
Reads invoices, receipts and bills for values, dates, quantities and serial numbers. These become the ground truth that later checks measure the claim against.

---

## 5. Phase 3: what is it worth, and is it covered?

### valuation_agent
Prices each item, then Python applies depreciation from `DEPRECIATION_BY_CATEGORY`:

| Category | Rate |
|---|---|
| Stock | 0 percent, trading stock is not depreciated |
| Valuables | 0 percent |
| Property | 5 percent |
| Furniture and fittings | 10 percent |
| Plant and machinery | 15 percent |
| Vehicle | 15 percent |
| Electronics | 25 percent |
| Anything else | 10 percent |

The formula is `net_loss = purchase_value * (1 - rate)`. These are placeholders, meant to be swapped for the insurer's real schedule.

If anything comes back unpriced, the router retries. After two attempts `valuation_fallback` zeroes those items and marks them as estimates, so one stubborn item cannot stall a whole claim.

### policy_agent
Decides coverage per item, tagging each as `COVERED`, `REVIEW` or `EXCLUDED`, citing the clause it relied on.

Then comes the check that matters. `_clause_is_grounded` verifies the cited clause actually appears in the real policy text, using a 0.6 similarity threshold. **A model that invents a supportive clause gets caught.** This is the anti hallucination control on the single most consequential AI output in the system.

### reconciliation_agent
Runs only when items are missing paperwork. Produces the list of what the claimant still needs to supply for those specific items.

### plausibility_check
The fraud screen. Pure Python, five checks per item:

1. **Chronology.** An item invoiced after the loss date did not exist when the loss happened.
2. **Invoice quantity ceiling.** Claiming 5 laptops against an invoice for 1 caps the claim at 1.
3. **Missing evidence.** An item with no photo backing it.
4. **Duplicate hash.** The same photo reused across claims.
5. **Duplicate serial.** The same serial number claimed twice.

Failing items move to a rejected annexure with reasons attached. They are recorded, not deleted. Separately, the total is checked against the policy sums insured and a warning is raised on a breach.

### reserve_estimate
Adds it up into six buckets: confirmed, conditional, excluded, unclassified, pending and screened out. The insurer sees the shape of the exposure, not one number.

---

## 6. Phase 4: write it up

### drafter
Writes the pack: a main schedule, a rejected items annexure, a pending verification annexure, an excluded items annexure and a narrative.

### qc_guardian
A second AI pass reading the first one's output, looking for sections citing items or clauses that do not exist. `_qc_section_mismatch` cross checks each section against the references it should contain.

Fail QC and it redrafts, up to two attempts. After that the pack **ships anyway**, but `send` writes every unresolved flag into the warnings. A known bad pack is never allowed to look clean, and a human reviews it downstream. Blocking forever would be worse than shipping with a visible flag.

### send and proof_of_intimation
`send` persists the pack. `proof_of_intimation` hashes it with SHA 256 and stores a receipt with the timestamp and recipient. That hash is what proves later which exact document was filed and when.

**Exit 3:** the successful finish.

---

## 7. Retry limits

Three places loop, each with a cap:

| Loop | Cap | What happens after |
|---|---|---|
| Valuation retry | 2 | Fall back to zero valued estimates |
| Drafter redraft | 2 | Ship the pack with QC flags attached |
| Requirement gate | none | Waits for the claimant, resumes on upload |

Nothing can spin forever.

---

## 8. Working on it

### Layout

| File | Holds |
|---|---|
| `graph.py` | Every node and router |
| `prompts.py` | Every prompt, all in one place |
| `schemas.py` | The Pydantic shapes models must return |
| `requirements.py` | The Letter of Requirement engine, no AI |
| `service.py` | FastAPI endpoints |
| `repository.py` | All database reads and writes |
| `models.py` | The 29 tables |
| `llm.py` | Provider switching and structured calls |
| `blobs.py` | Content addressed file storage |
| `images.py` | Turning stored files into image blocks for the model |

### Running the tests

```bash
cd agentic_pipeline
pytest -v
```

Some tests need Postgres. CI starts an ephemeral one. Locally:

```bash
docker compose up -d db
export TEST_POSTGRES_URL=postgresql+psycopg://suraksha:suraksha@localhost:5432/suraksha
```

The 19 test files map onto the gates: `test_fraud.py`, `test_plausibility.py`, `test_document_triage.py`, `test_identity.py`, `test_requirements.py` and so on. Because the gates are plain Python, they are all testable without calling a model.

### Switching model provider

Set `llm_provider` to `gemini` or `watsonx` and supply that provider's keys. Nothing else changes. `llm.py` handles it, and every call goes through `invoke_structured`, so responses are validated against a schema before any node sees them.

### Adding a node

1. Write the function in `graph.py`, taking `ClaimState` and returning a dict of updates.
2. If it calls a model, add its prompt to `prompts.py` and its output shape to `schemas.py`.
3. Register it with `graph_builder.add_node` and wire its edges.
4. Write the test.

Keep the split. If your node can reject something, it should not be the model making that call.
