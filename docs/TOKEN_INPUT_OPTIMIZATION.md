# CaptN-BRAIN Token Input Optimization

> **A layered system for understanding, controlling, and optimizing LLM input tokens.**

---

## Architecture Overview

```
Hermes Agent assembles request
       │
       ▼
┌─────────────────────────────────┐
│ 1. Token Profiler (Phase 1.5)   │  ← observation only
│    Measures every component:    │
│    system, history, memory,     │
│    tools, MCP, skills, etc.     │
│    NO behavior changes          │
└──────────────┬──────────────────┘
       │
       ▼
┌─────────────────────────────────┐
│ 2. Token Gap Analyzer (Ph1.6)  │  ← diagnostic
│    Compares local estimates     │
│    vs OpenRouter actual tokens  │
│    Accounts for caching,        │
│    serialization, tokenizer     │
└──────────────┬──────────────────┘
       │
       ▼
┌─────────────────────────────────┐
│ 3. Context Budget (Phase 2)     │  ← fixed 30K budget
│    Relevance-based selection    │
│    within category limits:      │
│    system=5K, history=8K,       │
│    memory=7K, tools=5K,         │
│    retrieved=5K                 │
│    Feature: CAPTN_CONTEXT_      │
│    BUDGET=on                    │
└──────────────┬──────────────────┘
       │
       ▼
┌─────────────────────────────────┐
│ 4. Adaptive Manager (Phase 3)   │  ← dynamic allocation
│    Complexity analysis           │
│    → adaptive budget planning   │
│    → delegates to ContextBudget │
│    Feature: CAPTN_CONTEXT_      │
│    BUDGET=adaptive              │
└──────────────┬──────────────────┘
       │
       ▼
┌─────────────────────────────────┐
│ 5. Hermes Profiler (Phase 1.5)  │  ← production telemetry
│    ProfileRecord (46 fields)    │
│    JSONL export, CLI querying   │
│    Budget + Adaptive telemetry  │
└─────────────────────────────────┘
       │
       ▼
       OpenRouter / LLM Provider
```

---

## Quick Reference

| Feature | Flag | Mode | Enables |
|---------|------|------|---------|
| **Off** | `CAPTN_CONTEXT_BUDGET=off` | Default | No budget, no selection |
| **Fixed Budget** | `CAPTN_CONTEXT_BUDGET=on` or `=fixed` | Observation + selection | 30K fixed allocation |
| **Adaptive Budget** | `CAPTN_CONTEXT_BUDGET=adaptive` | Observation + selection | Dynamic allocation by complexity |
| **Profiling** | `CAPTN_TOKEN_PROFILING=summary` | Observation only | Per-request attribution |
| **Profile Output** | `CAPTN_PROFILE_OUTPUT=/path/records.jsonl` | Persistence | Append-only JSONL |

### Quick Commands

```bash
# Budget status
python summon_agents.py devtools budget status

# Budget demo (dry run)
python summon_agents.py devtools budget demo

# Budget telemetry summary
python summon_agents.py devtools budget telemetry records

# Export telemetry
python summon_agents.py devtools budget telemetry export -o records.jsonl

# Adaptive demo (12 scenarios)
python summon_agents.py devtools adaptive demo

# Adaptive status
python summon_agents.py devtools adaptive status

# Hermes Profiler
python summon_agents.py devtools hermes_profile records
python summon_agents.py devtools hermes_profile csv

# Token Gap Investigation
python summon_agents.py devtools gap report
```

---

## 1. Token Profiler (Phase 1.5)

**File:** `captn/runtime/hermes_profiler.py`

Observation-only instrumentation that hooks into the Hermes Agent's `pre_api_request` lifecycle event — the last point before the request is sent to OpenRouter.

### What It Measures

- **Total estimated tokens** (chars/4 heuristic for messages + tools)
- **Per-category attribution**: system, conversation history, long-term memory, memory prefetch, skills index, MCP context, tool schemas, subagent definitions, user message, other
- **Attribution coverage**: percentage of total tokens that could be attributed to a known category
- **Actual provider usage**: input tokens, output tokens, cache read/write, from the provider response
- **Estimate error**: percentage difference between estimated and actual tokens

### Usage

```bash
# Enable profiling
export CAPTN_TOKEN_PROFILING=summary  # or: verbose

# View records
python summon_agents.py devtools hermes_profile records

# Export as CSV
python summon_agents.py devtools hermes_profile csv

# Continuous JSONL output
export CAPTN_PROFILE_OUTPUT=/tmp/profiles.jsonl
```

---

## 2. Token Gap Analyzer (Phase 1.6)

**File:** `captn/runtime/token_gap.py`

Diagnostic tool that reconciles local token estimates with OpenRouter's actual reported `input_tokens`. Addresses the discrepancy where Hermes estimates ~64K tokens but OpenRouter reports ~150K.

### Root Cause

The gap is explained by three factors:

1. **Token counting method**: `chars/4` heuristic → ~64K; local DeepSeek tokenizer → ~44K
2. **Provider prompt_tokens**: formula is `prompt_tokens = input_tokens + cache_read_tokens + cache_write_tokens`
3. **Cache accounting**: A 150K `prompt_tokens` value may be `80K uncached input + 50K cache read + 20K cache write`

### Usage

```bash
python summon_agents.py devtools gap report [--request REQ_ID]
python summon_agents.py devtools gap report --model "deepseek/deepseek-v4-flash"
```

---

## 3. OpenRouter Token Reconciliation (Phase 1.6.1)

**File:** `captn/runtime/reconcile.py`

Reconciles local tokenizer estimates against actual OpenRouter API response fields. Maps every field from the provider response to standardized categories.

### Key Accounting Identity

```
prompt_tokens = input_tokens + cache_read_tokens + cache_write_tokens
```

### Usage

```bash
python summon_agents.py devtools reconcile records
python summon_agents.py devtools reconcile report
```

---

## 4. Context Budget (Phase 2)

**Files:** `captn/runtime/context_budget.py`, `captn/cli/_devtools.py`

Prevents oversized prompts by selecting the most relevant content within configurable per-category token budgets.

### Architecture

```
Request
  │
  ├─ _select_system()      — immutable core never removed, optional sections (skills) dropped
  ├─ _select_history()     — recent + relevant + compressed older
  ├─ _select_memory()      — relevance-scored entries
  ├─ _select_tools()       — utility/token_cost ratio selection
  └─ _select_retrieved()   — BM25 relevance + token-cost top-K
  │
  ▼
SelectionResult (selected content + decisions + budget enforcement)
```

### Default Budget Configuration

| Category | Budget | Algorithm |
|----------|--------|-----------|
| **System** | 5,000 | Core instructions always kept. Optional sections (skills block, plugin context, ephemeral) dropped if needed. If core alone exceeds budget, `system_budget_exceeded=true`. |
| **History** | 8,000 | System message + current user message always kept. Non-system messages scored by recency (60%) + query relevance (keyword overlap, 30%). Greedy selection. Compression attempted if >3 messages dropped. |
| **Memory** | 7,000 | Content split into lines. Each line scored by query keyword overlap. Greedy selection within budget. |
| **Tools** | 5,000 | Pre-filtered by domain (from `_tool_domains.py`). Each tool scored by `utility/token_cost` ratio. Greedy selection. If any single mandatory tool exceeds budget, `tools_budget_exceeded=true`. |
| **Retrieved** | 5,000 | Content split into fragments (by double-newline). Scored by query keyword overlap. Greedy selection. Single fragment truncated if too large. |
| **Total** | **30,000** | Final check: if selected > budget, `total_budget_exceeded=true` |

### SelectionResult Fields

| Field | Description |
|-------|-------------|
| `original_*_tokens` | Token estimates before selection (per category + total) |
| `selected_*_tokens` | Token estimates after selection (per category + total) |
| `budget_*` | Budget limits for each category |
| `*_budget_exceeded` | Overflow flags for system, tools, total |
| `*_items_dropped` | Count of items removed per category |
| `decisions` | List of `SelectionDecision` with reasons |
| `selected_messages` / `selected_tools` / etc. | The actual selected content (for request construction) |
| `history_compressed` / `history_compressed_savings` | Whether compression was applied |
| `dry_run` | True if in observation-only mode |
| `overflow_tokens` (property) | `selected - budget` when exceeded |
| `unused_budget` (property) | `budget - selected` when under |

### Environment Variables

```bash
# Enable
export CAPTN_CONTEXT_BUDGET=on

# Configure (all optional, defaults shown)
export CAPTN_CONTEXT_BUDGET_TOTAL=30000
export CAPTN_CONTEXT_BUDGET_SYSTEM=5000
export CAPTN_CONTEXT_BUDGET_HISTORY=8000
export CAPTN_CONTEXT_BUDGET_MEMORY=7000
export CAPTN_CONTEXT_BUDGET_TOOLS=5000
export CAPTN_CONTEXT_BUDGET_RETRIEVED=5000
```

### CLI Commands

```bash
# Status
python summon_agents.py devtools budget status

# Demo (dry-run by default)
python summon_agents.py devtools budget demo

# Telemetry
python summon_agents.py devtools budget telemetry records
python summon_agents.py devtools budget telemetry record req_000042
python summon_agents.py devtools budget telemetry clear
python summon_agents.py devtools budget telemetry export -o records.jsonl
```

### Python API

```python
from captn.runtime.context_budget import (
    ContextBudget, BudgetConfig, SelectionResult,
    estimate_provider_tokens, is_budget_enabled,
)

budget = ContextBudget(model="deepseek/deepseek-v4-flash")
result = budget.select(
    system_prompt=system,
    messages=messages,
    memory_text=memory,
    tools=tools,
    retrieved_context=retrieved,
    user_query="What is the GCD of 48 and 180?",
    domain_hint="math",
    dry_run=True,  # observation only — no mutation
)
print(result.report())
# result.selected_messages → for the real request
# result.selected_tools   → for the real request
```

---

## 5. Adaptive Context Manager (Phase 3)

**File:** `captn/runtime/adaptive_manager.py`

Extends Context Budget with dynamic allocation: determines the optimal budget distribution per request based on deterministic complexity signals.

### Architecture

```
Request
  │
  ▼
ComplexityAnalyzer
  ├─ query_length / token_estimate
  ├─ history_depth (turns + volume)
  ├─ tool_count / schema_tokens
  ├─ memory/retrieval density
  ├─ context pressure (how close to 30K)
  └─ multi-dimension bonus (active categories)
  │
  ▼
Complexity Score ∈ [0, 1]
  ├─ simple:      0.00 – 0.35
  ├─ moderate:    0.35 – 0.60
  ├─ complex:     0.60 – 0.80
  └─ very_complex:0.80 – 1.00
  │
  ▼
AdaptiveBudgetPlanner
  ├─ Total budget: 30–100% of 30K based on complexity
  ├─ Category allocation: weighted by importance × complexity
  └─ Redistribution: unused tokens → highest-value categories
  │
  ▼
ContextBudget.select()  ← unchanged Phase 2 selector
```

### Complexity Score Weights

| Signal | Weight | Normalization |
|--------|--------|---------------|
| Query complexity | 0.20 | `len(query)/200`, `tokens/50` |
| History depth | 0.30 | `turns/50`, `tokens/20K` |
| Tool complexity | 0.20 | `count/50`, `tokens/10K` |
| Memory/retrieval density | 0.15 | `tokens/10K` each |
| Context pressure | 0.10 | `ctx_tokens/30K` |
| Multi-dimension bonus | 0.05 | active categories / 4 |

### Category Ranges (min–max)

| Category | Minimum | Maximum |
|----------|---------|---------|
| System | 3,000 | 5,000 |
| History | 2,000 | 8,000 |
| Memory | 1,000 | 7,000 |
| Tools | 1,000 | 5,000 |
| Retrieved | 1,000 | 5,000 |

### Strategy Labels

| Strategy | Trigger |
|----------|---------|
| `simple_conversation` | Complexity < 0.35 |
| `tool_heavy` | Tool count > 30 and schema > 8K |
| `tool_assisted` | Tool count > 15 and schema > 5K and score > 0.3 |
| `history_heavy` | Turn count > 40 |
| `memory_heavy` | Memory tokens > 15K |
| `retrieval_heavy` | Retrieved tokens > 10K |
| `extended_conversation` | Turns > 20 and score > 0.25 |
| `balanced` | Score 0.35–0.60 |
| `complex_analysis` | Score 0.60–0.80 |
| `very_complex_research` | Score ≥ 0.80 |

### Environment Variables

```bash
# Enable adaptive mode
export CAPTN_CONTEXT_BUDGET=adaptive

# Or use fixed mode (Phase 2 behavior)
export CAPTN_CONTEXT_BUDGET=fixed

# Disable entirely
export CAPTN_CONTEXT_BUDGET=off
```

### Fallback Chain

```
Adaptive (CAPTN_CONTEXT_BUDGET=adaptive)
    │ failure → CAPTN_CONTEXT_BUDGET=fixed
    ▼
Fixed (CAPTN_CONTEXT_BUDGET=fixed)
    │ failure → CAPTN_CONTEXT_BUDGET=off
    ▼
Baseline (no budget management)
```

All transitions are immediate — no code change, no restart required.

### Python API

```python
from captn.runtime.adaptive_manager import AdaptiveContextManager

am = AdaptiveContextManager(model="deepseek/deepseek-v4-flash")
result = am.select(
    system_prompt=system,
    messages=messages,
    memory_text=memory,
    tools=tools,
    retrieved_context=retrieved,
    user_query="What is GCD?",
    domain_hint="math",
    dry_run=True,
)
print(f"Complexity: {result.complexity_score:.3f} ({result.complexity_label})")
print(f"Strategy:   {result.budget_strategy}")
print(f"Budget:     {result.adaptive_total_budget}")
print(f"Allocation: sys={result.system_allocated} hist={result.history_allocated} "
      f"mem={result.memory_allocated} tools={result.tools_allocated} ret={result.retrieved_allocated}")

# Access underlying SelectionResult
sel = result.selection
print(f"Selected tokens: {sel.selected_total_tokens}")
```

### CLI Commands

```bash
# Show adaptive configuration
python summon_agents.py devtools adaptive status

# Run demo across 12 scenarios
python summon_agents.py devtools adaptive demo
```

---

## 6. ProfileRecord — Telemetry (46 fields)

**File:** `captn/runtime/hermes_profiler.py`

Every profiled request produces a `ProfileRecord` — a flat, machine-readable structure that captures all token, budget, and adaptive metrics.

### Field Groups

#### Identity (5 fields)
`request_id`, `timestamp`, `model`, `provider`, `session_id`

#### Estimates (3 fields)
`estimated_total`, `estimated_messages`, `estimated_tools`

#### Counts (2 fields)
`message_count`, `tool_count`

#### Category Attribution (10 fields)
`system_tokens`, `history_tokens`, `memory_tokens`, `memory_prefetch_tokens`, `skills_tokens`, `mcp_tokens`, `tool_schema_tokens`, `subagent_tokens`, `current_user_tokens`, `other_tokens`

#### Attribution Quality (2 fields)
`attributed_total`, `attribution_coverage`

#### Actual Provider Usage (5 fields)
`actual_input_tokens`, `actual_output_tokens`, `actual_total_tokens`, `cache_read_tokens`, `cache_write_tokens`

#### Budget Telemetry (6 fields)
`budget_enabled`, `budget_before_tokens`, `budget_after_tokens`, `budget_drop_count`, `budget_warnings`, `system_budget_exceeded`

#### Adaptive Telemetry (11 fields)
`adaptive_enabled`, `complexity_score`, `complexity_class`, `adaptive_total_budget`, `adaptive_system_budget`, `adaptive_history_budget`, `adaptive_memory_budget`, `adaptive_tools_budget`, `adaptive_retrieved_budget`, `adaptive_redistribution_count`, `adaptive_strategy`

#### Diagnostics (2 fields)
`oversized_items`, `estimate_error_pct`

### Aggregated Metrics

```python
from captn.runtime.hermes_profiler import (
    get_all_records, records_summary, budget_summary, records_csv
)

# Full summary
summary = records_summary()
print(summary["total_requests"])
print(summary["budget"])
print(summary["categories"])
print(summary["estimated"])
print(summary["actual"])

# Budget summary
budget = budget_summary()
print(budget["total_before"], budget["total_after"])
print(budget["avg_reduction_pct"], budget["p50_reduction_pct"])

# CSV export
csv_data = records_csv()
```

---

## 7. Hyperparameters & Tuning

### Complexity Score Weights (definitive)

| Component | Weight | Environment Override |
|-----------|--------|---------------------|
| Query | 0.20 | N/A (code constant) |
| History | 0.30 | N/A |
| Tools | 0.20 | N/A |
| Density | 0.15 | N/A |
| Pressure | 0.10 | N/A |
| Multi | 0.05 | N/A |

### Category Importance Weights

| Category | Weight |
|----------|--------|
| System | 0.25 |
| History | 0.20 |
| Memory | 0.20 |
| Tools | 0.20 |
| Retrieved | 0.15 |

### Complexity Thresholds

| Class | Range | Budget Ratio |
|-------|-------|-------------|
| Simple | [0.00, 0.35) | 30–55% of max |
| Moderate | [0.35, 0.60) | 55–70% of max |
| Complex | [0.60, 0.80) | 70–85% of max |
| Very Complex | [0.80, 1.00] | 85–100% of max |

---

## 8. Validation & Benchmark History

| Phase | Tests | Pass | Key Result |
|-------|-------|------|------------|
| Phase 2 (Context Budget) | 15 | ✓ | Budget selection within limits |
| Phase 2.1 (Hardening) | 15 | ✓ | Boundary, overflow, cache accounting |
| Phase 2.2 (Staging) | 15 | ✓ | 19.9% avg reduction, 100% success |
| Phase 2.3 (Observability) | 11+12 | ✓ | ProfileRecord budget fields |
| Phase 2.4 (Production Gate) | 22 | ✓ | 22.2% reduction, p95=51ms |
| Phase 3 (Adaptive Manager) | 22 | ✓ | 4.0% savings vs Fixed, 100% quality |
| Phase 3.1 (Staging) | 12 | ✓ | 4.0% savings, p95=7.5ms overhead |
| Phase 3.2 (Calibration) | 19 | ✓ | 2.7% savings, score corr=1.0 |

### Key A/B Results (Fixed vs Adaptive)

| Metric | Fixed | Adaptive | Delta |
|--------|-------|----------|-------|
| Total tokens (12 scenarios) | 42,943 | 41,222 | **-4.0%** |
| Success rate | 100% | 100% | 0pp |
| Selection p50 | 3.9ms | 5.7ms | +1.8ms |
| Selection p95 | 25.8ms | 33.7ms | +7.9ms |
| >30K fixture (46K orig) | 26,568 | 26,286 | -282 tokens |

---

## 9. File Reference

| File | LOC | Purpose |
|------|-----|---------|
| `captn/runtime/hermes_profiler.py` | ~1,300 | Production token profiler + telemetry |
| `captn/runtime/context_budget.py` | ~1,170 | Context Budget selector and CLI |
| `captn/runtime/adaptive_manager.py` | ~850 | Adaptive Context Manager |
| `captn/runtime/token_gap.py` | ~850 | Token gap investigation |
| `captn/runtime/reconcile.py` | ~400 | OpenRouter token reconciliation |
| `captn/cli/_devtools.py` | ~140 | CLI registration for all tools |
| `tools/test_*.py` (7 files) | ~2,900 | 106 comprehensive tests |

---

## 10. Safety & Privacy

- **Feature flag OFF by default** — no production impact until explicitly enabled
- **Zero prompt content in telemetry** — only token counts, counts, and structural metadata
- **Immediate rollback** — `export CAPTN_CONTEXT_BUDGET=off` with no code change
- **No ML, no embeddings** — all decisions are deterministic and explainable
- **Cache-safe** — system prompt prefix remains stable; adaptive only affects dynamic content
- **All tests pass**: 106/106 across 7 test suites