# WhatsApp Chatbot Analytics & AI Intelligence Layer

A two-layer Databricks project: a PySpark analytics pipeline over synthetic WhatsApp
customer-service conversations, and a Claude API layer that turns those analytics into
AI-generated insights, classifies conversations, and prototypes a hybrid (AI + rules)
support bot. Built to demonstrate data engineering and generative AI integration together,
not as two separate portfolio pieces.

## Architecture

```
bi/                              ai/
├── 00_data_setup.py             ├── 00_ARCHITECTURE.md
├── 01_bi_analytics_dashboard.py ├── 01_claude_api_setup.py
└── 02_export_bi_summary.py      ├── 02_business_intelligence_assistant.py
                                  ├── 03_conversation_classifier.py
                                  └── 04_hybrid_bot_prototype.py
```

`bi/` generates and analyzes the data with plain PySpark -- no external calls, cheap to
run repeatedly. `ai/` depends on `bi/`'s output (via a small summary table, not a direct
notebook dependency) and is where Claude API calls happen. The split is deliberate: the
expensive, iterative debugging work happens in the free layer; the paid layer only runs
once things are already correct.

## What each module demonstrates

**`01_claude_api_setup.py`** -- Foundation utilities shared by every AI module: rate
limiting, retry with exponential backoff, in-memory response caching, a cost estimator,
and a connectivity test that fails fast and clearly if the cluster can't reach the API
(rather than surfacing as a confusing downstream error).

**`02_business_intelligence_assistant.py`** -- Turns computed analytics into an
AI-written executive summary. The one rule this module enforces strictly: Python computes
every number, Claude only narrates. A post-response check extracts every number in
Claude's output and flags any that don't trace back to the input metrics -- a cheap,
concrete guard against the most common LLM failure mode (inventing a plausible statistic).
Dollar-value ROI is off by default and only appears if real assumptions are explicitly
supplied, since the underlying data is synthetic.

**`03_conversation_classifier.py`** -- Samples a stratified set of real conversations and
classifies intent/sentiment/frustration with Claude, compared against a rule-based regex
classifier. Framed explicitly as an *agreement rate*, not an accuracy measurement, since
neither side has been checked against human judgment -- with an export path to a labeling
table if a real accuracy number is wanted later. Cost is estimated and printed before any
API calls run.

**`04_hybrid_bot_prototype.py`** -- A working bot prototype where intent detection calls
Claude first and automatically falls back to the original regex classifier if the API call
fails or returns something unparseable. That fallback path is the actual "hybrid" in the
name -- not just an AI feature with a rules-based baseline for comparison, but a system
with a safety net. Negation and menu-digit detection deliberately stayed rule-based, since
an API call adds latency and cost with no accuracy benefit on an exact-match check.

## Real findings (from the actual test run, synthetic data)

- 43.05% of conversations end within 3 messages -- matches the generator's own configured
  target, confirming the pipeline computes from live data rather than echoing stale numbers.
- 80.4% of conversations end with the bot sending the last message (i.e. the user simply
  stops responding).
- Menu option 1 accounts for 32.5% of all tracked selections.
- 19.3% of user messages contain a negation ("cancel"/"no"/"stop"); 67,512 conversations
  show multiple negations (frustration signal).
- AI vs. rule-based classifier: only 30% agreement on intent, ~57.5% agreement on
  sentiment and frustration. The intent gap is the most interesting result -- the
  rule-based classifier only fires on exact keyword matches, while Claude infers intent
  from context even without the keyword present. That gap is the strongest concrete
  argument for the AI layer, and it's measured, not assumed.
- Sample classification run: 40 conversations, $0.07.

*(All figures are from synthetic data generated for this project, not real customer
interactions -- stated explicitly in every notebook that surfaces them.)*

## Engineering decisions worth explaining in an interview

**Cost control was designed in, not bolted on.** Data generation is guarded behind a
`FORCE_REGENERATE` flag that checks whether the table already exists before doing any
work; every batch classification run prints a cost estimate before making a single API
call; development happened on a free tier, with the paid cloud reserved for a single,
predictable final run.

**Guardrails against LLM unreliability, not trust.** Numbers are computed in Python and
only narrated by Claude, checked afterward for hallucinated figures. Agreement between AI
and rule-based classification is reported as agreement, not accuracy, because there's no
ground truth to call either one "correct." ROI in dollars requires explicit, named
assumptions rather than letting the model invent a number.

**Production patterns**, not just a demo script: secrets never hardcoded (Databricks
Secrets, cluster-scoped), rate limiting and exponential backoff on retries, response
caching to avoid double-billing on a rerun, and a hybrid fallback so an API outage
degrades the bot's behavior instead of crashing it.

**Real debugging, worth mentioning if asked.** Three separate root causes surfaced while
getting the AI layer running on Azure: a `dbutils.library.restartPython()` inside a
`%run`-chained notebook silently broke variable propagation to the calling notebook (fixed
by moving the SDK to a cluster-scoped library instead of notebook-scoped `%pip`); a
missing `typing_extensions` version mismatch caused a cryptic `TypedDict` error inside the
Anthropic SDK itself (fixed by pinning a compatible version as a second cluster library);
and pasting notebook source directly into a single UI cell silently no-oped every
`%run`/magic command in it, since Databricks only treats `# COMMAND ----------` as a cell
boundary when the file is actually imported, not pasted (fixed by using Import instead of
copy-paste). Each of these produced the same downstream symptom and required isolating the
actual point of failure rather than guessing.

## Known limitations

- Data is synthetic and was generated without a fixed random seed, so re-running the
  generator produces statistically similar but not identical data across environments.
- The 30%/57.5% agreement figures are agreement rates between two unverified classifiers,
  not accuracy against ground truth -- `03_conversation_classifier.py` includes an export
  step for manual labeling if a real accuracy number is needed.
- The in-memory response cache resets on cluster restart; a production version would
  persist it to a Delta table.

## Skills demonstrated

**Data engineering:** PySpark pipeline design, Delta Lake, Unity Catalog, cost-aware
architecture (sampling, caching, guarded regeneration), debugging across free-tier and
paid-tier network/library constraints.

**Generative AI integration:** prompt engineering for structured output, hallucination
guardrails, cost estimation and rate limiting for API-backed pipelines, hybrid
AI-plus-rules system design with graceful degradation.

**Production thinking:** secrets management, retry/backoff patterns, agreement-vs-accuracy
rigor, explicit and auditable assumptions instead of black-box outputs.
