#!/usr/bin/env python3
"""
adaptive_manager.py — Adaptive Context Manager for CaptN Context Budget.

Determines dynamic budget allocation per request based on deterministic
complexity signals, then delegates to the fixed ContextBudget selector.

Architecture:

    Request
      ↓
    ComplexityAnalyzer  ← deterministic signals
      ↓
    AdaptiveBudgetPlanner  ← min/max bounds, redistribution
      ↓
    ContextBudget.select()  ← unchanged from Phase 2
      ↓
    SelectionResult  (+ adaptive telemetry fields)

Usage:
    from captn.runtime.adaptive_manager import AdaptiveContextManager

    am = AdaptiveContextManager(model="deepseek/deepseek-v4-flash")
    result = am.select(
        system_prompt=...,
        messages=...,
        memory_text=...,
        tools=...,
        retrieved_context=...,
        user_query="what is gcd?",
        domain_hint="math",
        dry_run=True,
    )
    print(result.report())
    print(f"Complexity: {result.complexity_score:.2f}")
    print(f"Strategy:   {result.budget_strategy}")
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("adaptive_manager")

# ═══════════════════════════════════════════════════════════════════
# CONSTANTS — Configurable defaults
# ═══════════════════════════════════════════════════════════════════

# Complexity thresholds (configurable)
_SIMPLE_THRESHOLD = 0.35
_MODERATE_THRESHOLD = 0.60
_COMPLEX_THRESHOLD = 0.80

# Budget ranges per category (min, max)
_CATEGORY_RANGES: Dict[str, Tuple[int, int]] = {
    "system":    (3_000, 5_000),     # system is mostly fixed
    "history":   (2_000, 8_000),     # varies with conversation depth
    "memory":    (1_000, 7_000),     # varies with relevance
    "tools":     (1_000, 5_000),     # varies with tool count
    "retrieved": (1_000, 5_000),     # varies with retrieval volume
}

# Default weights for importance (higher = more budget when available)
_CATEGORY_IMPORTANCE: Dict[str, float] = {
    "system":    0.25,
    "history":   0.20,
    "memory":    0.20,
    "tools":     0.20,
    "retrieved": 0.15,
}

_CATEGORY_NAMES = ["system", "history", "memory", "tools", "retrieved"]

# ═══════════════════════════════════════════════════════════════════
# COMPLEXITY ANALYZER
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ComplexitySignals:
    """Deterministic signals used to compute task complexity."""
    query_length: int = 0
    query_token_estimate: int = 0
    history_available_tokens: int = 0
    history_message_count: int = 0
    memory_available_tokens: int = 0
    memory_entry_count: int = 0
    tool_count: int = 0
    tool_schema_tokens: int = 0
    retrieved_tokens: int = 0
    retrieved_fragment_count: int = 0
    conversation_turn_count: int = 0
    current_context_tokens: int = 0
    system_tokens: int = 0
    domain_hint_present: bool = False
    multi_tool: bool = False       # >1 tool
    has_memory: bool = False
    has_retrieval: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query_length": self.query_length,
            "query_tokens": self.query_token_estimate,
            "history_tokens": self.history_available_tokens,
            "history_msgs": self.history_message_count,
            "memory_tokens": self.memory_available_tokens,
            "memory_entries": self.memory_entry_count,
            "tool_count": self.tool_count,
            "tool_tokens": self.tool_schema_tokens,
            "retrieved_tokens": self.retrieved_tokens,
            "retrieved_frags": self.retrieved_fragment_count,
            "turn_count": self.conversation_turn_count,
            "context_tokens": self.current_context_tokens,
            "system_tokens": self.system_tokens,
            "domain_hint": self.domain_hint_present,
            "multi_tool": self.multi_tool,
            "has_memory": self.has_memory,
            "has_retrieval": self.has_retrieval,
        }


def compute_signals(
    system_prompt: str = "",
    messages: Optional[List[Dict[str, Any]]] = None,
    memory_text: str = "",
    tools: Optional[List[Dict[str, Any]]] = None,
    retrieved_context: str = "",
    user_query: str = "",
    domain_hint: str = "",
) -> ComplexitySignals:
    """Extract deterministic complexity signals from request components.

    All signals are derived from the content itself — no embeddings, no ML.
    """
    from captn.runtime.context_budget import estimate_provider_tokens, estimate_messages_tokens, estimate_tool_tokens

    msgs = messages or []
    tools_list = tools or []

    # Query signals
    query_len = len(user_query)
    query_tok = estimate_provider_tokens(user_query)

    # History signals
    hist_tok = estimate_messages_tokens(msgs)
    # Count user+assistant turns (pairs)
    non_system = [m for m in msgs if m.get("role") != "system"]
    turn_count = sum(1 for m in non_system if m.get("role") == "user")

    # Memory signals
    mem_tok = estimate_provider_tokens(memory_text)
    mem_entries = max(1, memory_text.count("\n")) if memory_text else 0

    # Tool signals
    tool_tok = estimate_tool_tokens(tools_list)
    tool_cnt = len(tools_list)

    # Retrieval signals
    ret_tok = estimate_provider_tokens(retrieved_context)
    ret_frags = max(1, retrieved_context.count("\n\n")) if retrieved_context else 0

    # System
    sys_tok = estimate_provider_tokens(system_prompt)

    # Current context
    ctx_tok = sys_tok + hist_tok + mem_tok + tool_tok + ret_tok

    return ComplexitySignals(
        query_length=query_len,
        query_token_estimate=query_tok,
        history_available_tokens=hist_tok,
        history_message_count=len(msgs),
        memory_available_tokens=mem_tok,
        memory_entry_count=mem_entries,
        tool_count=tool_cnt,
        tool_schema_tokens=tool_tok,
        retrieved_tokens=ret_tok,
        retrieved_fragment_count=ret_frags,
        conversation_turn_count=turn_count,
        current_context_tokens=ctx_tok,
        system_tokens=sys_tok,
        domain_hint_present=bool(domain_hint),
        multi_tool=tool_cnt > 1,
        has_memory=mem_tok > 0,
        has_retrieval=ret_tok > 0,
    )


def compute_complexity(signals: ComplexitySignals) -> float:
    """Compute a deterministic task complexity score in [0.0, 1.0].

    The score aggregates multiple sub-scores:

    - Query complexity: longer/more tokens → higher
    - Context depth: more history, more turns → higher
    - Tool complexity: many tools → higher
    - Memory/retrieval density: substantial content → higher

    Each sub-score is bounded [0, 1]. Final score is weighted sum.
    """
    # 1. Query complexity (0.0–0.2 weight)
    q_len = min(signals.query_length / 200, 1.0)
    q_tok = min(signals.query_token_estimate / 50, 1.0)
    query_score = 0.7 * q_len + 0.3 * q_tok  # [0, 1]

    # 2. History depth (0.0–0.3 weight)
    hist_depth = min(signals.conversation_turn_count / 50, 1.0)
    hist_volume = min(signals.history_available_tokens / 20_000, 1.0)
    history_score = 0.5 * hist_depth + 0.5 * hist_volume  # [0, 1]

    # 3. Tool complexity (0.0–0.2 weight)
    tool_count_score = min(signals.tool_count / 50, 1.0)
    tool_volume = min(signals.tool_schema_tokens / 10_000, 1.0)
    tool_score = 0.5 * tool_count_score + 0.5 * tool_volume  # [0, 1]

    # 4. Memory/retrieval density (0.0–0.15 weight)
    mem_density = min(signals.memory_available_tokens / 10_000, 1.0)
    ret_density = min(signals.retrieved_tokens / 10_000, 1.0)
    density_score = 0.5 * mem_density + 0.5 * ret_density  # [0, 1]

    # 5. Context pressure (0.0–0.10 weight) — how close to 30K
    ctx_tok = signals.current_context_tokens
    ctx_pressure = min(ctx_tok / 30_000, 1.0)  # [0, 1]

    # 6. Multi-dimension (0.0–0.05) — bonus for having multiple active categories
    active = sum([
        signals.has_memory,
        signals.has_retrieval,
        signals.multi_tool,
        signals.domain_hint_present,
    ])
    multi_score = min(active / 4.0, 1.0)

    # Weighted combination
    complexity = (
        0.20 * query_score +
        0.30 * history_score +
        0.20 * tool_score +
        0.15 * density_score +
        0.10 * ctx_pressure +
        0.05 * multi_score
    )

    # Clamp
    return max(0.0, min(1.0, complexity))


def classify_complexity(score: float) -> str:
    """Classify a complexity score into a human-readable label."""
    if score < _SIMPLE_THRESHOLD:
        return "simple"
    elif score < _MODERATE_THRESHOLD:
        return "moderate"
    elif score < _COMPLEX_THRESHOLD:
        return "complex"
    else:
        return "very_complex"


# ═══════════════════════════════════════════════════════════════════
# ADAPTIVE BUDGET PLANNER
# ═══════════════════════════════════════════════════════════════════

@dataclass
class AdaptiveBudgetPlan:
    """Result of the adaptive budget planner."""
    complexity_score: float = 0.0
    complexity_label: str = ""
    total_budget: int = 30_000
    budget_strategy: str = "adaptive"

    # Allocated per category
    system: int = 0
    history: int = 0
    memory: int = 0
    tools: int = 0
    retrieved: int = 0

    # Available content (for redistribution tracking)
    available_system: int = 0
    available_history: int = 0
    available_memory: int = 0
    available_tools: int = 0
    available_retrieved: int = 0

    # Redistribution
    redistribution_count: int = 0
    redistribution_reason: str = ""

    # Signal snapshot
    signals: Optional[ComplexitySignals] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "complexity_score": round(self.complexity_score, 4),
            "complexity_label": self.complexity_label,
            "total_budget": self.total_budget,
            "budget_strategy": self.budget_strategy,
            "allocation": {
                "system": self.system,
                "history": self.history,
                "memory": self.memory,
                "tools": self.tools,
                "retrieved": self.retrieved,
            },
            "available": {
                "system": self.available_system,
                "history": self.available_history,
                "memory": self.available_memory,
                "tools": self.available_tools,
                "retrieved": self.available_retrieved,
            },
            "redistribution_count": self.redistribution_count,
            "redistribution_reason": self.redistribution_reason,
        }


def _infer_strategy(signals: ComplexitySignals, score: float) -> str:
    """Infer a human-readable budget strategy from signals and complexity.

    Check tool/memory/retrieval signals first (they are independent of
    conversation complexity), then fall through to complexity-based labels.
    """
    if signals.tool_count > 30 and signals.tool_schema_tokens > 8000:
        return "tool_heavy"
    if signals.tool_count > 15 and signals.tool_schema_tokens > 5000 and score > 0.3:
        return "tool_assisted"
    if signals.conversation_turn_count > 40:
        return "history_heavy"
    if signals.memory_available_tokens > 15000:
        return "memory_heavy"
    if signals.retrieved_tokens > 10000:
        return "retrieval_heavy"
    if signals.conversation_turn_count > 20 and score > 0.25:
        return "extended_conversation"
    if score < _SIMPLE_THRESHOLD:
        return "simple_conversation"
    if score < _MODERATE_THRESHOLD:
        return "balanced"
    if score < _COMPLEX_THRESHOLD:
        return "complex_analysis"
    return "very_complex_research"


def plan_budget(
    signals: ComplexitySignals,
    max_total: int = 30_000,
) -> AdaptiveBudgetPlan:
    """Compute an adaptive budget allocation.

    Steps:
    1. Compute complexity score.
    2. Determine total budget on [max_total * 0.3, max_total] based on complexity.
    3. Distribute initially by category importance.
    4. Apply category min/max bounds.
    5. Redistribute unused tokens to the highest-importance categories.
    6. Return the final plan.
    """
    score = compute_complexity(signals)
    label = classify_complexity(score)

    # Target total: scale with complexity
    # simple → ~30% of max, very_complex → 100%
    budget_ratio = 0.3 + 0.7 * score
    total = max(5_000, min(max_total, int(max_total * budget_ratio)))
    total = int(round(total / 1000) * 1000)  # round to nearest 1K

    # Available content per category
    avail = {
        "system": signals.system_tokens,
        "history": signals.history_available_tokens,
        "memory": signals.memory_available_tokens,
        "tools": signals.tool_schema_tokens,
        "retrieved": signals.retrieved_tokens,
    }

    # 1. System is special: allocate what's needed, capped at max
    sys_alloc = min(max(_CATEGORY_RANGES["system"][0], avail["system"]), total // 2)
    remaining = total - sys_alloc

    # 2. Distribute remaining among history, memory, tools, retrieved
    # Weighted by importance × complexity
    categories = ["history", "memory", "tools", "retrieved"]
    weights = {}
    for cat in categories:
        base = _CATEGORY_IMPORTANCE.get(cat, 0.15)
        # Boost tools and memory for complex tasks
        if cat == "tools" and signals.multi_tool and score > 0.5:
            base *= 1.3
        if cat == "memory" and signals.has_memory and score > 0.5:
            base *= 1.2
        if cat == "history" and signals.conversation_turn_count > 20:
            base *= 1.2
        weights[cat] = base
    total_w = sum(weights.values())

    # Initial allocation: weighted share of remaining
    alloc = {}
    for cat in categories:
        raw = int(remaining * weights[cat] / total_w)
        min_b, max_b = _CATEGORY_RANGES[cat]
        alloc[cat] = max(min_b, min(max_b, raw))

    alloc["system"] = sys_alloc

    # 3a. Cap each category to available content (don't reserve what doesn't exist)
    for cat in _CATEGORY_NAMES:
        alloc[cat] = min(alloc[cat], max(avail.get(cat, 0), _CATEGORY_RANGES[cat][0]))

    # 3b. Check total and redistribute
    current_sum = sum(alloc[cat] for cat in _CATEGORY_NAMES)
    redistribution_count = 0
    redistribution_reason = ""

    if current_sum < total:
        # Underfilled — redistribute unused to highest-importance categories
        surplus = total - current_sum
        redistribution_count = surplus
        redistribution_reason = f"underfilled: {surplus} tokens redistributed"

        # Feed to categories with available content exceeding allocation
        candidates = []
        for cat in categories:
            _, max_b = _CATEGORY_RANGES[cat]
            if alloc[cat] < avail[cat] and alloc[cat] < max_b:
                room = min(avail[cat], max_b) - alloc[cat]
                candidates.append((_CATEGORY_IMPORTANCE.get(cat, 0.1), cat, room))
        candidates.sort(key=lambda x: -x[0])

        for _, cat, room in candidates:
            give = min(surplus, room)
            if give <= 0:
                continue
            alloc[cat] += give
            surplus -= give
            redistribution_count += give
            if surplus <= 0:
                break

    elif current_sum > total:
        # Overallocated — cut proportionally
        excess = current_sum - total
        redistribution_count = -excess
        redistribution_reason = f"overallocated: trimmed {excess} tokens"

        # Cut from categories with least importance (reverse order)
        candidates = sorted(categories, key=lambda c: _CATEGORY_IMPORTANCE.get(c, 0.1))
        for cat in candidates:
            min_b, _ = _CATEGORY_RANGES[cat]
            can_cut = alloc[cat] - min_b
            cut = min(excess, can_cut)
            if cut <= 0:
                continue
            alloc[cat] -= cut
            excess -= cut
            if excess <= 0:
                break

    # 4. Remove redundant clamp (already done in step 3a)
    # for cat in _CATEGORY_NAMES:
    #     alloc[cat] = min(alloc[cat], max(avail.get(cat, 0), alloc[cat]))

    strategy = _infer_strategy(signals, score)

    return AdaptiveBudgetPlan(
        complexity_score=score,
        complexity_label=label,
        total_budget=total,
        budget_strategy=strategy,
        system=alloc["system"],
        history=alloc["history"],
        memory=alloc["memory"],
        tools=alloc["tools"],
        retrieved=alloc["retrieved"],
        available_system=signals.system_tokens,
        available_history=signals.history_available_tokens,
        available_memory=signals.memory_available_tokens,
        available_tools=signals.tool_schema_tokens,
        available_retrieved=signals.retrieved_tokens,
        redistribution_count=redistribution_count,
        redistribution_reason=redistribution_reason,
        signals=signals,
    )


# ═══════════════════════════════════════════════════════════════════
# ADAPTIVE CONTEXT MANAGER
# ═══════════════════════════════════════════════════════════════════

@dataclass
class AdaptiveSelectionResult:
    """Wraps a SelectionResult with adaptive telemetry."""
    # The underlying fixed-budget result
    selection: Any = None

    # Adaptive plan
    adaptive_enabled: bool = True
    complexity_score: float = 0.0
    complexity_label: str = ""
    adaptive_total_budget: int = 30_000
    budget_strategy: str = "adaptive"

    # Allocations
    system_allocated: int = 0
    history_allocated: int = 0
    memory_allocated: int = 0
    tools_allocated: int = 0
    retrieved_allocated: int = 0

    # Signals
    signals: Optional[ComplexitySignals] = None

    # Performance
    complexity_ms: float = 0.0
    planning_ms: float = 0.0
    selection_ms: float = 0.0

    # Redistribution
    redistribution_count: int = 0
    redistribution_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        base = self.selection.to_dict() if self.selection else {}
        return {
            **base,
            "adaptive": {
                "enabled": self.adaptive_enabled,
                "complexity_score": round(self.complexity_score, 4),
                "complexity_label": self.complexity_label,
                "adaptive_total_budget": self.adaptive_total_budget,
                "budget_strategy": self.budget_strategy,
                "allocation": {
                    "system": self.system_allocated,
                    "history": self.history_allocated,
                    "memory": self.memory_allocated,
                    "tools": self.tools_allocated,
                    "retrieved": self.retrieved_allocated,
                },
                "redistribution_count": self.redistribution_count,
                "redistribution_reason": self.redistribution_reason,
                "latency_ms": {
                    "complexity": round(self.complexity_ms, 2),
                    "planning": round(self.planning_ms, 2),
                    "selection": round(self.selection_ms, 2),
                },
            },
        }

    def report(self) -> str:
        sep = "─" * 52
        lines = [sep, "  ADAPTIVE CONTEXT MANAGER", sep]
        lines.append(f"  Strategy:      {self.budget_strategy}")
        lines.append(f"  Complexity:    {self.complexity_score:.3f} ({self.complexity_label})")
        lines.append(f"  Total budget:  {self.adaptive_total_budget:,}")
        lines.append(f"  Redistributed: {self.redistribution_count:,} ({self.redistribution_reason})")
        lines.append(sep)
        lines.append(f"  {'System':20s} {self.system_allocated:>6,d} allocated")
        lines.append(f"  {'History':20s} {self.history_allocated:>6,d} allocated")
        lines.append(f"  {'Memory':20s} {self.memory_allocated:>6,d} allocated")
        lines.append(f"  {'Tools':20s} {self.tools_allocated:>6,d} allocated")
        lines.append(f"  {'Retrieved':20s} {self.retrieved_allocated:>6,d} allocated")
        lines.append(sep)
        lines.append(f"  Latency:  {self.complexity_ms + self.planning_ms + self.selection_ms:.2f}ms "
                     f"(complexity={self.complexity_ms:.1f}ms + planning={self.planning_ms:.1f}ms + selection={self.selection_ms:.1f}ms)")
        if self.selection:
            lines.append("")
            lines.append(self.selection.report())
        else:
            lines.append(sep)
        return "\n".join(lines)


class AdaptiveContextManager:
    """Adaptive Context Manager for CaptN.

    Pipeline:
        1. Complexity Analysis  (deterministic signals → complexity score)
        2. Adaptive Budget Planning (plan allocation)
        3. ContextBudget.select() (delegates to fixed Phase 2 selector)
        4. Wraps result with adaptive telemetry
    """

    def __init__(
        self,
        model: str = "",
        max_total_budget: int = 30_000,
    ):
        self.model = model
        self.max_total_budget = max_total_budget

    def select(
        self,
        *,
        system_prompt: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
        memory_text: str = "",
        tools: Optional[List[Dict[str, Any]]] = None,
        retrieved_context: str = "",
        user_query: str = "",
        domain_hint: str = "",
        dry_run: bool = False,
    ) -> AdaptiveSelectionResult:
        """Run the full adaptive pipeline.

        1. Extract signals → compute complexity
        2. Plan adaptive budget
        3. Delegate to ContextBudget.select() with adaptive config
        4. Return AdaptiveSelectionResult
        """
        from captn.runtime.context_budget import ContextBudget, BudgetConfig

        # ── 1. Complexity Analysis ──
        t0 = time.perf_counter()
        signals = compute_signals(
            system_prompt=system_prompt,
            messages=messages,
            memory_text=memory_text,
            tools=tools,
            retrieved_context=retrieved_context,
            user_query=user_query,
            domain_hint=domain_hint,
        )
        t1 = time.perf_counter()
        complexity_ms = (t1 - t0) * 1000

        # ── 2. Adaptive Planning ──
        plan = plan_budget(signals, max_total=self.max_total_budget)
        t2 = time.perf_counter()
        planning_ms = (t2 - t1) * 1000

        # ── 3. Delegation to ContextBudget ──
        config = BudgetConfig(
            total=plan.total_budget,
            system=plan.system,
            history=plan.history,
            memory=plan.memory,
            tools=plan.tools,
            retrieved=plan.retrieved,
        )
        budget = ContextBudget(model=self.model, config=config)
        result = budget.select(
            system_prompt=system_prompt,
            messages=messages,
            memory_text=memory_text,
            tools=tools,
            retrieved_context=retrieved_context,
            user_query=user_query,
            domain_hint=domain_hint,
            dry_run=dry_run,
        )
        t3 = time.perf_counter()
        selection_ms = (t3 - t2) * 1000

        return AdaptiveSelectionResult(
            selection=result,
            adaptive_enabled=True,
            complexity_score=plan.complexity_score,
            complexity_label=plan.complexity_label,
            adaptive_total_budget=plan.total_budget,
            budget_strategy=plan.budget_strategy,
            system_allocated=plan.system,
            history_allocated=plan.history,
            memory_allocated=plan.memory,
            tools_allocated=plan.tools,
            retrieved_allocated=plan.retrieved,
            signals=signals,
            complexity_ms=complexity_ms,
            planning_ms=planning_ms,
            selection_ms=selection_ms,
            redistribution_count=plan.redistribution_count,
            redistribution_reason=plan.redistribution_reason,
        )


# ═══════════════════════════════════════════════════════════════════
# FEATURE FLAG — extends is_budget_enabled semantics
# ═══════════════════════════════════════════════════════════════════

ENV_BUDGET = "CAPTN_CONTEXT_BUDGET"


def get_budget_mode() -> str:
    """Return the current budget mode: 'off', 'fixed', or 'adaptive'."""
    raw = os.environ.get(ENV_BUDGET, "off").strip().lower()
    if raw in ("adaptive", "auto"):
        return "adaptive"
    if raw in ("on", "1", "true", "yes", "fixed"):
        return "fixed"
    return "off"


def is_budget_enabled() -> bool:
    """Return True if any budget mode is active."""
    return get_budget_mode() != "off"


def is_adaptive_enabled() -> bool:
    """Return True only if adaptive mode is active."""
    return get_budget_mode() == "adaptive"


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

def register_cli(subparsers) -> None:
    """Register adaptive CLI subcommand under devtools."""
    p = subparsers.add_parser(
        "adaptive",
        help="Adaptive Context Manager — dynamic budget allocation based on complexity",
    )
    sub = p.add_subparsers(dest="adaptive_cmd")

    demo_p = sub.add_parser("demo", help="Show adaptive allocation on demo payloads")
    demo_p.set_defaults(func=_cmd_adaptive_demo)

    status_p = sub.add_parser("status", help="Show adaptive configuration")
    status_p.set_defaults(func=_cmd_adaptive_status)


def _cmd_adaptive_demo(args) -> None:
    """Show adaptive allocation on demo payloads."""
    print("\n  ADAPTIVE CONTEXT MANAGER — DEMO")
    print(f"  {'─'*52}")
    print(f"\n  Running 12 scenarios to demonstrate allocations...\n")

    demos = [
        ("simple_q", "What is Python?", "",
         [{"role": "user", "content": "Hi"}], "", [], "", "coding"),
        ("short_conv", "What is the GCD of 48 and 180?", "",
         [{"role": "system", "content": "You are helpful."},
          {"role": "user", "content": "What is Python?"},
          {"role": "assistant", "content": "Python is a language."},
          {"role": "user", "content": "What is GCD?"}],
         "", [], "", "math"),
        ("long_conv", "Explain recursion", "",
         [{"role": "system", "content": "You are CaptN." * 20},
          *sum(([{"role": "user", "content": f"Turn {i}"},
                  {"role": "assistant", "content": f"Response {i}"}]
                 for i in range(30)), [])],
         "User memory. " * 200,
         [{"type": "function", "function": {"name": f"t{i}", "description": f"A tool {i}", "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}} for i in range(20)],
         "GCD info. " * 50, "math"),
        ("tool_heavy", "Calculate results", "",
         [{"role": "system", "content": "You are CaptN." * 30},
          {"role": "user", "content": "Run calculations"}],
         "",
         [{"type": "function", "function": {"name": f"tool_{i}", "description": f"Tool {i} with extra text filler to increase size" * 5, "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}} for i in range(40)],
         "", "math"),
        ("memory_heavy", "Recall previous work", "",
         [{"role": "system", "content": "You are CaptN." * 20},
          {"role": "user", "content": "What did we discuss?"},
          {"role": "assistant", "content": "We discussed Python."},
          {"role": "user", "content": "Details please."}],
         "Previous session data. " * 500 + "Key insight from last time. " * 50,
         [{"type": "function", "function": {"name": "t0", "description": "A tool", "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}}],
         "", "coding"),
        ("retrieval_heavy", "Explain quantum computing", "",
         [{"role": "system", "content": "You are CaptN." * 20},
          {"role": "user", "content": "Explain quantum"}],
         "",
         [],
         "Quantum computing basics. " * 200 + "Detailed quantum theory. " * 150,
         "science"),
        ("combined", "Solve this complex problem", "",
         [{"role": "system", "content": "You are CaptN." * 50},
          *sum(([{"role": "user", "content": f"Turn {i}" * 5},
                  {"role": "assistant", "content": f"Response {i}" * 8}]
                 for i in range(40)), [])],
         "Comprehensive memory. " * 300,
         [{"type": "function", "function": {"name": f"t{i}", "description": f"Tool {i}" * 5, "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}} for i in range(30)],
         "Extensive retrieval content. " * 100,
         "coding"),
        ("near_limit", "Complex analysis", "",
         [{"role": "system", "content": "You are CaptN." * 80},
          *sum(([{"role": "user", "content": f"Turn {i} with longer content." * 5},
                  {"role": "assistant", "content": f"Response {i} with more detail." * 8}]
                 for i in range(50)), [])],
         "Memory of previous analysis. " * 250,
         [{"type": "function", "function": {"name": f"t{i}", "description": f"Tool {i}" * 5, "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}} for i in range(25)],
         "Analysis context. " * 80,
         "coding"),
    ]

    print(f"  {'ID':20s} {'Score':>6s} {'Label':>13s} {'Budget':>8s} {'Strategy':>20s} {'Lat':>7s}")
    print(f"  {'─'*20:>20s} {'─'*6:>6s} {'─'*13:>13s} {'─'*8:>8s} {'─'*20:>20s} {'─'*7:>7s}")
    for sid, query, system, msgs, mem, tools, ret, dh in demos:
        am = AdaptiveContextManager(model="test")
        result = am.select(
            system_prompt=system, messages=msgs,
            memory_text=mem, tools=tools,
            retrieved_context=ret, user_query=query,
            domain_hint=dh, dry_run=True,
        )
        total_ms = result.complexity_ms + result.planning_ms + result.selection_ms
        selected = result.selection.selected_total_tokens if result.selection else 0
        print(f"  {sid:20s} {result.complexity_score:>5.3f}  {result.complexity_label:>13s} "
              f"{selected:>6,d}/{result.adaptive_total_budget:>5,d} "
              f"{result.budget_strategy:>20s} {total_ms:>5.1f}ms")
        if sid in ("simple_q", "tool_heavy", "combined"):
            alloc = result.to_dict().get("adaptive", {}).get("allocation", {})
            print(f"  {'':20s} alloc: sys={alloc.get('system',0)} hist={alloc.get('history',0)} "
                  f"mem={alloc.get('memory',0)} tools={alloc.get('tools',0)} ret={alloc.get('retrieved',0)}")
    print()


def _cmd_adaptive_status(args) -> None:
    """Show adaptive configuration status."""
    mode = get_budget_mode()
    enabled = is_budget_enabled()
    adaptive = is_adaptive_enabled()

    print(f"  Adaptive Context Manager")
    print(f"  {'─'*52}")
    print(f"  {'Mode':25s} {mode}")
    print(f"  {'Budget enabled':25s} {'Yes' if enabled else 'No'}")
    print(f"  {'Adaptive enabled':25s} {'Yes' if adaptive else 'No'}")
    print(f"  {'Max total budget':25s} 30,000 tokens")
    print()
    print(f"  Category ranges (min-max):")
    for cat in _CATEGORY_NAMES:
        lo, hi = _CATEGORY_RANGES[cat]
        print(f"    {cat:12s} {lo:>5,d} – {hi:>5,d} tokens")
    print()
    print(f"  Complexity thresholds:")
    print(f"    Simple:        0.00 – {_SIMPLE_THRESHOLD}")
    print(f"    Moderate:      {_SIMPLE_THRESHOLD} – {_MODERATE_THRESHOLD}")
    print(f"    Complex:       {_MODERATE_THRESHOLD} – {_COMPLEX_THRESHOLD}")
    print(f"    Very complex:  {_COMPLEX_THRESHOLD} – 1.00")
    print()
    print(f"  Enable:")
    print(f"    export CAPTN_CONTEXT_BUDGET=adaptive")
    print(f"    python summon_agents.py devtools adaptive demo")


# ═══════════════════════════════════════════════════════════════════
__all__ = [
    "AdaptiveContextManager", "AdaptiveSelectionResult",
    "AdaptiveBudgetPlan", "ComplexitySignals", "ComplexityAnalyzer",
    "compute_signals", "compute_complexity", "plan_budget",
    "get_budget_mode", "is_budget_enabled", "is_adaptive_enabled",
    "classify_complexity",
]
# Alias for external use
ComplexityAnalyzer = compute_complexity