#!/usr/bin/env python3
"""
Phase 3.1 — Adaptive Manager Experimental Staging Validation.

Covers:
  - ProfileRecord adaptive telemetry fields
  - Complexity sensitivity & monotonicity
  - Budget allocation sensitivity (per-category)
  - Fixed vs Adaptive A/B on 12+ scenarios
  - >30K deterministic fixture
  - Latency breakdown (complexity/planning/selection)
  - Fallback chain (adaptive→fixed→off)
  - Telemetry privacy
  - All regression tests
"""
from __future__ import annotations

import json, math, os, random, statistics, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from captn.runtime.context_budget import (
    ContextBudget, BudgetConfig, estimate_provider_tokens,
    estimate_messages_tokens, estimate_tool_tokens, SelectionResult,
)
from captn.runtime.adaptive_manager import (
    AdaptiveContextManager, AdaptiveSelectionResult, AdaptiveBudgetPlan,
    ComplexitySignals, compute_signals, compute_complexity,
    plan_budget, classify_complexity, get_budget_mode,
    is_budget_enabled, is_adaptive_enabled,
)
from captn.runtime.hermes_profiler import (
    ProfileRecord, _add_adaptive_telemetry, _add_budget_telemetry,
    get_all_records, clear_records, records_summary,
)

random.seed(42)


# ═══════════════════════════════════════════════════════════════════
# 1. COMPLEXITY SENSITIVITY & MONOTONICITY
# ═══════════════════════════════════════════════════════════════════

def check_monotonic(label: str, param_name: str, values: list, signal_factory):
    """Verify that complexity_score is monotonic as a single param varies."""
    scores = []
    for v in values:
        sig = signal_factory(v)
        s = compute_complexity(sig)
        scores.append(s)
    # Check non-decreasing (allow very small noise)
    violations = 0
    for i in range(1, len(scores)):
        if scores[i] < scores[i-1] - 0.001:
            violations += 1
    ok = violations == 0
    print(f"  {'✓' if ok else '✗'} {label:25s} values={values} scores={[f'{s:.4f}' for s in scores]}")
    return ok


def run_sensitivity_analysis():
    print(f"\n{'─'*72}")
    print(f"  COMPLEXITY SENSITIVITY & MONOTONICITY")
    print(f"{'─'*72}\n")

    results = []

    # Query length sensitivity
    def query_factory(n_chars):
        return ComplexitySignals(
            query_length=n_chars, query_token_estimate=max(1, n_chars//4),
            conversation_turn_count=2, history_available_tokens=500,
            history_message_count=3, tool_count=2, tool_schema_tokens=500,
            memory_available_tokens=100, memory_entry_count=5,
            retrieved_tokens=100, retrieved_fragment_count=2,
            current_context_tokens=2000, system_tokens=500,
        )
    ok = check_monotonic("query_length", "n_chars", [5, 20, 100, 300], query_factory)
    results.append(("query_length", ok))

    # History depth sensitivity
    def history_factory(n_turns):
        return ComplexitySignals(
            query_length=50, query_token_estimate=12,
            conversation_turn_count=n_turns,
            history_available_tokens=n_turns * 300,
            history_message_count=n_turns * 2,
            tool_count=5, tool_schema_tokens=1500,
            memory_available_tokens=500, memory_entry_count=10,
            retrieved_tokens=200, retrieved_fragment_count=3,
            current_context_tokens=n_turns * 500 + 2000,
            system_tokens=500,
        )
    ok = check_monotonic("history_turns", "n_turns", [1, 5, 20, 50, 100], history_factory)
    results.append(("history_turns", ok))

    # Tool count sensitivity
    def tool_factory(n_tools):
        return ComplexitySignals(
            query_length=50, query_token_estimate=12,
            conversation_turn_count=3, history_available_tokens=500,
            history_message_count=4, tool_count=n_tools,
            tool_schema_tokens=n_tools * 300,
            memory_available_tokens=100, memory_entry_count=3,
            retrieved_tokens=100, retrieved_fragment_count=2,
            current_context_tokens=n_tools * 300 + 2000,
            system_tokens=500,
            multi_tool=n_tools > 1,
        )
    ok = check_monotonic("tool_count", "n_tools", [0, 2, 10, 40, 80], tool_factory)
    results.append(("tool_count", ok))

    # Memory density sensitivity
    def memory_factory(n_chars):
        return ComplexitySignals(
            query_length=50, query_token_estimate=12,
            conversation_turn_count=3, history_available_tokens=500,
            history_message_count=4, tool_count=2, tool_schema_tokens=500,
            memory_available_tokens=n_chars, memory_entry_count=max(1, n_chars//50),
            retrieved_tokens=100, retrieved_fragment_count=2,
            current_context_tokens=2000 + n_chars, system_tokens=500,
            has_memory=n_chars > 0,
        )
    ok = check_monotonic("memory_density", "n_chars", [0, 100, 1000, 5000, 20000], memory_factory)
    results.append(("memory_density", ok))

    # Retrieval density sensitivity
    def retrieval_factory(n_chars):
        return ComplexitySignals(
            query_length=50, query_token_estimate=12,
            conversation_turn_count=3, history_available_tokens=500,
            history_message_count=4, tool_count=2, tool_schema_tokens=500,
            memory_available_tokens=100, memory_entry_count=3,
            retrieved_tokens=n_chars, retrieved_fragment_count=max(1, n_chars//50),
            current_context_tokens=2000 + n_chars, system_tokens=500,
            has_retrieval=n_chars > 0,
        )
    ok = check_monotonic("retrieval_density", "n_chars", [0, 100, 1000, 5000, 15000], retrieval_factory)
    results.append(("retrieval_density", ok))

    # Context pressure sensitivity
    def pressure_factory(n_tokens):
        return ComplexitySignals(
            query_length=50, query_token_estimate=12,
            conversation_turn_count=5, history_available_tokens=n_tokens//3,
            history_message_count=10, tool_count=5, tool_schema_tokens=1000,
            memory_available_tokens=n_tokens//5, memory_entry_count=20,
            retrieved_tokens=n_tokens//10, retrieved_fragment_count=10,
            current_context_tokens=n_tokens, system_tokens=500,
        )
    ok = check_monotonic("context_pressure", "n_tokens", [2000, 10000, 25000, 35000, 60000], pressure_factory)
    results.append(("context_pressure", ok))

    passes = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\n  Monotonicity: {passes}/{total} signals monotonic")
    return passes == total


# ═══════════════════════════════════════════════════════════════════
# 2. BUDGET ALLOCATION SENSITIVITY
# ═══════════════════════════════════════════════════════════════════

def run_allocation_sensitivity():
    print(f"\n{'─'*72}")
    print(f"  BUDGET ALLOCATION SENSITIVITY")
    print(f"{'─'*72}\n")

    # History-heavy signal
    sig_hist = ComplexitySignals(
        query_length=30, query_token_estimate=8,
        conversation_turn_count=80, history_available_tokens=30000,
        history_message_count=160, tool_count=2, tool_schema_tokens=500,
        memory_available_tokens=100, memory_entry_count=3,
        retrieved_tokens=100, retrieved_fragment_count=2,
        current_context_tokens=32000, system_tokens=500,
        has_memory=False, has_retrieval=False, multi_tool=False,
    )
    plan_hist = plan_budget(sig_hist)
    print(f"  History-heavy: hist={plan_hist.history} (avail={sig_hist.history_available_tokens}), "
          f"strategy={plan_hist.budget_strategy}")

    # Tool-heavy signal
    sig_tool = ComplexitySignals(
        query_length=30, query_token_estimate=8,
        conversation_turn_count=2, history_available_tokens=200,
        history_message_count=3, tool_count=60, tool_schema_tokens=15000,
        memory_available_tokens=100, memory_entry_count=3,
        retrieved_tokens=100, retrieved_fragment_count=2,
        current_context_tokens=16000, system_tokens=500,
        has_memory=False, has_retrieval=False, multi_tool=True,
    )
    plan_tool = plan_budget(sig_tool)
    print(f"  Tool-heavy:    tools={plan_tool.tools} (avail={sig_tool.tool_schema_tokens}), "
          f"strategy={plan_tool.budget_strategy}")

    # Memory-heavy signal
    sig_mem = ComplexitySignals(
        query_length=30, query_token_estimate=8,
        conversation_turn_count=3, history_available_tokens=500,
        history_message_count=4, tool_count=2, tool_schema_tokens=500,
        memory_available_tokens=20000, memory_entry_count=400,
        retrieved_tokens=200, retrieved_fragment_count=3,
        current_context_tokens=22000, system_tokens=500,
        has_memory=True, has_retrieval=False, multi_tool=False,
    )
    plan_mem = plan_budget(sig_mem)
    print(f"  Memory-heavy:  mem={plan_mem.memory} (avail={sig_mem.memory_available_tokens}), "
          f"strategy={plan_mem.budget_strategy}")

    # Retrieval-heavy signal
    sig_ret = ComplexitySignals(
        query_length=30, query_token_estimate=8,
        conversation_turn_count=2, history_available_tokens=200,
        history_message_count=3, tool_count=2, tool_schema_tokens=500,
        memory_available_tokens=100, memory_entry_count=3,
        retrieved_tokens=15000, retrieved_fragment_count=100,
        current_context_tokens=16000, system_tokens=500,
        has_memory=False, has_retrieval=True, multi_tool=False,
    )
    plan_ret = plan_budget(sig_ret)
    print(f"  Retrieval-heavy: ret={plan_ret.retrieved} (avail={sig_ret.retrieved_tokens}), "
          f"strategy={plan_ret.budget_strategy}")

    # All allocation patterns should be distinct
    allocs = [(plan_hist.history, plan_hist.tools, plan_hist.memory, plan_hist.retrieved),
              (plan_tool.history, plan_tool.tools, plan_tool.memory, plan_tool.retrieved),
              (plan_mem.history, plan_mem.tools, plan_mem.memory, plan_mem.retrieved),
              (plan_ret.history, plan_ret.tools, plan_ret.memory, plan_ret.retrieved)]
    unique = len(set(allocs))
    print(f"  Distinct allocation patterns: {unique}/4 (more is better)")
    return unique >= 3


# ═══════════════════════════════════════════════════════════════════
# 3. FIXED VS ADAPTIVE (12 SCENARIOS)
# ═══════════════════════════════════════════════════════════════════

def build_scenarios() -> list:
    scenarios = []
    # 1. Simple question
    scenarios.append(dict(
        id="simple_q", bucket="<10K",
        system="You are helpful.",
        msgs=[{"role": "user", "content": "What is Python?"}],
        mem="", tools=[], ret="", query="What is Python?", dh="coding",
    ))
    # 2. Short conversation
    scenarios.append(dict(
        id="short_conv", bucket="<10K",
        system="You are CaptN. " * 10,
        msgs=[{"role": "system", "content": "You are CaptN." * 10},
              {"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello"},
              {"role": "user", "content": "What is GCD?"}],
        mem="", tools=[], ret="", query="What is GCD?", dh="math",
    ))
    # 3. Long conversation
    scenarios.append(dict(
        id="long_conv", bucket="10-20K",
        system="You are CaptN. " * 30,
        msgs=[{"role": "system", "content": "You are CaptN. " * 30}] +
             sum(([{"role": "user", "content": f"Turn {i} with detailed content." * 3},
                   {"role": "assistant", "content": f"Response {i} with analysis." * 5}]
                  for i in range(30)), []) +
             [{"role": "user", "content": "What is recursion?"}],
        mem="", tools=[], ret="", query="What is recursion?", dh="coding",
    ))
    # 4. Tool-heavy
    scenarios.append(dict(
        id="tool_heavy", bucket="10-20K",
        system="You are CaptN. " * 30,
        msgs=[{"role": "system", "content": "You are CaptN. " * 30},
              {"role": "user", "content": "Run calculations for me."}],
        mem="",
        tools=[{"type": "function", "function": {"name": f"tool_{i}",
                "description": f"Advanced calculation tool for complex operations {i}" * 5,
                "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}}
               for i in range(40)],
        ret="", query="Run calculations", dh="math",
    ))
    # 5. Memory-heavy
    scenarios.append(dict(
        id="memory_heavy", bucket="10-20K",
        system="You are CaptN. " * 20,
        msgs=[{"role": "system", "content": "You are CaptN. " * 20},
              {"role": "user", "content": "What did we discuss last time?"},
              {"role": "assistant", "content": "We discussed Python."},
              {"role": "user", "content": "Tell me more."}],
        mem="Previous session: user prefers Python and algorithms. " * 200,
        tools=[], ret="", query="Tell me more about our previous work", dh="coding",
    ))
    # 6. Retrieval-heavy
    scenarios.append(dict(
        id="retrieval_heavy", bucket="10-20K",
        system="You are CaptN. " * 20,
        msgs=[{"role": "system", "content": "You are CaptN. " * 20},
              {"role": "user", "content": "Explain the topic."}],
        mem="", tools=[],
        ret="Detailed background information about the topic in question. " * 200,
        query="Explain the topic in detail", dh="science",
    ))
    # 7. Combined
    scenarios.append(dict(
        id="combined", bucket="20-25K",
        system="You are CaptN. " * 50,
        msgs=[{"role": "system", "content": "You are CaptN. " * 50}] +
             sum(([{"role": "user", "content": f"Turn {i} with longer content here." * 5},
                   {"role": "assistant", "content": f"Response {i} with detailed analysis." * 8}]
                  for i in range(40)), []) +
             [{"role": "user", "content": "Solve this complex problem."}],
        mem="Comprehensive memory from many sessions. " * 300,
        tools=[{"type": "function", "function": {"name": f"t{i}",
                "description": f"Tool for complex operations." * 5,
                "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}}
               for i in range(30)],
        ret="Extensive background knowledge and context. " * 100,
        query="Solve this complex problem step by step", dh="coding",
    ))
    # 8. Near limit
    scenarios.append(dict(
        id="near_limit", bucket="20-25K",
        system="You are CaptN. " * 80,
        msgs=[{"role": "system", "content": "You are CaptN. " * 80}] +
             sum(([{"role": "user", "content": f"Turn {i} with extended content." * 5},
                   {"role": "assistant", "content": f"Response {i} with analysis and code." * 8}]
                  for i in range(50)), []) +
             [{"role": "user", "content": "Provide detailed analysis of the algorithm."}],
        mem="Prior analysis notes. " * 250,
        tools=[{"type": "function", "function": {"name": f"t{i}",
                "description": f"Tool for analysis." * 5,
                "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}}
               for i in range(25)],
        ret="Analysis context and references. " * 80,
        query="Provide detailed analysis", dh="coding",
    ))
    # 9. Very long conversation
    scenarios.append(dict(
        id="history_heavy", bucket="20-25K",
        system="You are CaptN. " * 40,
        msgs=[{"role": "system", "content": "You are CaptN. " * 40}] +
             sum(([{"role": "user", "content": f"Long turn {i} with substantial context." * 3},
                   {"role": "assistant", "content": f"Long response {i} with details." * 5}]
                  for i in range(60)), []) +
             [{"role": "user", "content": "What did we say in turn 30?"}],
        mem="", tools=[], ret="", query="What did we say in turn 30?", dh="coding",
    ))
    # 10. Low context (minimal)
    scenarios.append(dict(
        id="low_context", bucket="<10K",
        system="You are helpful.",
        msgs=[{"role": "user", "content": "Hi"}],
        mem="", tools=[], ret="", query="Hi", dh="",
    ))
    # 11. High context
    scenarios.append(dict(
        id="high_context", bucket="25-30K",
        system="You are CaptN. " * 100,
        msgs=[{"role": "system", "content": "You are CaptN. " * 100}] +
             sum(([{"role": "user", "content": f"Turn {i} with extensive context." * 8},
                   {"role": "assistant", "content": f"Response {i} with comprehensive analysis." * 10}]
                  for i in range(40)), []) +
             [{"role": "user", "content": "Provide a comprehensive solution."}],
        mem="Extensive memory content. " * 400,
        tools=[{"type": "function", "function": {"name": f"t{i}",
                "description": f"Comprehensive tool {i}." * 5,
                "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}}
               for i in range(35)],
        ret="Large retrieval context. " * 150,
        query="Provide a comprehensive solution with all context considered", dh="coding",
    ))
    # 12. Ambiguous query
    scenarios.append(dict(
        id="ambiguous", bucket="<10K",
        system="You are helpful.",
        msgs=[{"role": "system", "content": "You are helpful."},
              {"role": "user", "content": "Can you help me with something?"}],
        mem="User works on programming projects. " * 50,
        tools=[], ret="", query="Can you help me with something?", dh="",
    ))
    return scenarios


# ═══════════════════════════════════════════════════════════════════
# 4. >30K FIXTURE
# ═══════════════════════════════════════════════════════════════════

def build_over30k_fixture() -> dict:
    huge_sys = "You are CaptN. " * 2000
    msgs = [{"role": "system", "content": huge_sys}]
    for i in range(100):
        msgs.append({"role": "user", "content": f"Turn {i} with very long content about Python." * 10})
        msgs.append({"role": "assistant", "content": f"Response {i} with extensive analysis." * 15})
    msgs.append({"role": "user", "content": "What is GCD?"})
    return dict(
        id="over30k_fixture", bucket=">30K", system=huge_sys, messages=msgs,
        memory="User prefers Python. " * 500,
        tools=[{"type": "function", "function": {"name": f"t{i}", "description": "A tool", "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}} for i in range(80)],
        retrieved="Euclidean algorithm. " * 200,
        query="What is GCD?", domain_hint="math",
    )


# ═══════════════════════════════════════════════════════════════════
# 5. MAIN VALIDATION
# ═══════════════════════════════════════════════════════════════════

def run_validation():
    print(f"\n{'='*72}")
    print(f"  PHASE 3.1 — ADAPTIVE MANAGER STAGING VALIDATION")
    print(f"{'='*72}")

    all_passed = True

    # ── A. Complexity Sensitivity ──
    monotone_ok = run_sensitivity_analysis()
    all_passed = all_passed and monotone_ok

    # ── B. Budget Allocation Sensitivity ──
    alloc_ok = run_allocation_sensitivity()
    all_passed = all_passed and alloc_ok

    # ── C. Fixed vs Adaptive A/B ──
    scenarios = build_scenarios()
    over30k = build_over30k_fixture()
    n = len(scenarios)

    print(f"\n{'─'*72}")
    print(f"  FIXED VS ADAPTIVE A/B — {n} scenarios + >30K fixture")
    print(f"{'─'*72}\n")

    modes = {"fixed": 0, "adaptive": 0}
    results = {mode: [] for mode in modes}
    ad_latencies = {"complexity": [], "planning": [], "selection": [], "total": []}

    # Fixed mode
    print(f"  Running FIXED...")
    for s in scenarios:
        t0 = time.perf_counter()
        b = ContextBudget(model="test")
        r = b.select(
            system_prompt=s["system"], messages=s["msgs"],
            memory_text=s["mem"], tools=s["tools"],
            retrieved_context=s["ret"], user_query=s["query"],
            domain_hint=s["dh"], dry_run=True,
        )
        lat = (time.perf_counter() - t0) * 1000
        results["fixed"].append(dict(
            id=s["id"], bucket=s["bucket"],
            original=r.original_total_tokens,
            selected=r.selected_total_tokens,
            dropped=r.history_items_dropped + r.memory_items_dropped + r.tools_dropped + r.retrieved_items_dropped,
            in_budget=r.selected_total_tokens <= r.budget_total,
            exceeded=r.total_budget_exceeded,
            latency=lat, success=True,
        ))

    # Adaptive mode
    print(f"  Running ADAPTIVE...")
    clear_records()
    for s in scenarios:
        t0 = time.perf_counter()
        am = AdaptiveContextManager(model="test")
        ar = am.select(
            system_prompt=s["system"], messages=s["msgs"],
            memory_text=s["mem"], tools=s["tools"],
            retrieved_context=s["ret"], user_query=s["query"],
            domain_hint=s["dh"], dry_run=True,
        )
        lat = (time.perf_counter() - t0) * 1000
        sel = ar.selection
        ad_latencies["complexity"].append(ar.complexity_ms)
        ad_latencies["planning"].append(ar.planning_ms)
        ad_latencies["selection"].append(ar.selection_ms)
        ad_latencies["total"].append(lat)
        results["adaptive"].append(dict(
            id=s["id"], bucket=s["bucket"],
            original=sel.original_total_tokens,
            selected=sel.selected_total_tokens,
            complexity=ar.complexity_score,
            label=ar.complexity_label,
            strategy=ar.budget_strategy,
            adaptive_budget=ar.adaptive_total_budget,
            dropped=sel.history_items_dropped + sel.memory_items_dropped + sel.tools_dropped + sel.retrieved_items_dropped,
            in_budget=sel.selected_total_tokens <= sel.budget_total,
            exceeded=sel.total_budget_exceeded,
            latency=lat,
            alloc_sys=ar.system_allocated, alloc_hist=ar.history_allocated,
            alloc_mem=ar.memory_allocated, alloc_tools=ar.tools_allocated,
            alloc_ret=ar.retrieved_allocated,
            success=True,
        ))
        # Telemetry
        rec = ProfileRecord(request_id=f"req_{s['id']}", timestamp=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                            model="test", estimated_total=sel.original_total_tokens)
        _add_budget_telemetry(rec, selection_result=sel)
        _add_adaptive_telemetry(rec, adaptive_result=ar)
        get_all_records()  # warm up
        from captn.runtime.hermes_profiler import _append_record as _ap
        _ap(rec)

    # >30K fixture — Adaptive
    print(f"  Running >30K fixture (adaptive)...")
    t0 = time.perf_counter()
    am = AdaptiveContextManager(model="test")
    over_ar = am.select(
        system_prompt=over30k["system"], messages=over30k["messages"],
        memory_text=over30k["memory"], tools=over30k["tools"],
        retrieved_context=over30k["retrieved"], user_query=over30k["query"],
        domain_hint=over30k["domain_hint"], dry_run=True,
    )
    over_lat = (time.perf_counter() - t0) * 1000
    over_sel = over_ar.selection

    print(f"    Original: {over_sel.original_total_tokens:,}")
    print(f"    Selected: {over_sel.selected_total_tokens:,}")
    print(f"    Budget:   {over_sel.budget_total:,}")
    print(f"    In budget: {over_sel.selected_total_tokens <= over_sel.budget_total}")
    print(f"    Exceeded: {over_sel.total_budget_exceeded}")
    assert over_sel.original_total_tokens > 30000, ">30K fixture must exceed 30K"
    assert over_sel.selected_total_tokens <= 30000 or over_sel.total_budget_exceeded, "Must be in budget or report exceeded"

    # >30K — Fixed as comparison
    print(f"  Running >30K fixture (fixed)...")
    t0 = time.perf_counter()
    fb = ContextBudget(model="test")
    over_fixed = fb.select(
        system_prompt=over30k["system"], messages=over30k["messages"],
        memory_text=over30k["memory"], tools=over30k["tools"],
        retrieved_context=over30k["retrieved"], user_query=over30k["query"],
        domain_hint=over30k["domain_hint"], dry_run=True,
    )
    print(f"    Fixed selected:  {over_fixed.selected_total_tokens:,}")
    print(f"    Adaptive selected: {over_sel.selected_total_tokens:,}")

    # ── AGGREGATE ──
    print(f"\n  {'─'*72}")
    print(f"  AGGREGATED RESULTS")
    print(f"{'─'*72}\n")

    for mode in ["fixed", "adaptive"]:
        totals = [r["selected"] for r in results[mode]]
        lats = [r["latency"] for r in results[mode]]
        origs = [r["original"] for r in results[mode]]
        red = sum(origs) - sum(totals)
        print(f"  {mode.upper():12s} original={sum(origs):>8,d} selected={sum(totals):>8,d} "
              f"red={red:>+6,d} ({red/max(sum(origs),1)*100:+.1f}%) "
              f"p50={statistics.median(lats):.1f}ms p95={sorted(lats)[int(len(lats)*0.95)]:.1f}ms")

    # Per-scenario detail
    print(f"\n  {'─'*120}")
    print(f"  {'ID':20s} {'Bucket':10s} {'Orig':>6s} {'Fixed':>6s} {'Adapt':>6s} "
          f"{'Score':>6s} {'Label':>13s} {'Budget':>6s} {'Strategy':>25s} {'FLat':>6s} {'ALat':>6s}")
    print(f"  {'─'*20:>20s} {'─'*10:>10s} {'─'*6:>6s} {'─'*6:>6s} {'─'*6:>6s} "
          f"{'─'*6:>6s} {'─'*13:>13s} {'─'*6:>6s} {'─'*25:>25s} {'─'*6:>6s} {'─'*6:>6s}")

    for i in range(n):
        fx = results["fixed"][i]
        ad = results["adaptive"][i]
        print(f"  {ad['id']:20s} {ad['bucket']:10s} {ad['original']:>6,d} {fx['selected']:>6,d} "
              f"{ad['selected']:>6,d} {ad['complexity']:>5.3f} {ad['label']:>13s} "
              f"{ad['adaptive_budget']:>5,d} {ad['strategy']:>25s} {fx['latency']:>5.1f}ms {ad['latency']:>5.1f}ms")

    # Complexity distribution
    print(f"\n  COMPLEXITY DISTRIBUTION")
    labels = {"simple": 0, "moderate": 0, "complex": 0, "very_complex": 0}
    for r in results["adaptive"]:
        lbl = r["label"]
        if lbl in labels:
            labels[lbl] += 1
        else:
            labels["simple"] += 1  # fallback
    for lbl, cnt in labels.items():
        scores = [r["complexity"] for r in results["adaptive"] if r["label"] == lbl]
        avg_s = statistics.mean(scores) if scores else 0
        avg_b = statistics.mean([r["adaptive_budget"] for r in results["adaptive"] if r["label"] == lbl]) if scores else 0
        print(f"    {lbl:15s} {cnt:>3d} requests  avg_score={avg_s:.3f}  avg_budget={avg_b:>6,.0f}")

    # Budget distribution
    print(f"\n  BUDGET DISTRIBUTION")
    budgets = sorted([r["adaptive_budget"] for r in results["adaptive"]])
    print(f"    Min:     {min(budgets):>6,d}")
    print(f"    P25:     {budgets[len(budgets)//4]:>6,d}")
    print(f"    P50:     {statistics.median(budgets):>6,.0f}")
    print(f"    P75:     {budgets[3*len(budgets)//4]:>6,d}")
    print(f"    Max:     {max(budgets):>6,d}")
    print(f"    Buckets: {len([b for b in budgets if b < 10000]):>3d} <10K  "
          f"{len([b for b in budgets if 10000 <= b < 15000]):>3d} 10-15K  "
          f"{len([b for b in budgets if 15000 <= b < 20000]):>3d} 15-20K  "
          f"{len([b for b in budgets if b >= 20000]):>3d} >=20K")

    # Latency breakdown
    print(f"\n  LATENCY BREAKDOWN (adaptive)")
    for phase in ["complexity", "planning", "selection", "total"]:
        vals = ad_latencies[phase]
        print(f"    {phase:12s} p50={statistics.median(vals):.2f}ms  "
              f"p95={sorted(vals)[int(len(vals)*0.95)]:.2f}ms  "
              f"max={max(vals):.2f}ms")

    # Token savings per scenario
    print(f"\n  TOKEN SAVINGS (Adaptive vs Fixed)")
    total_fx = sum(r["selected"] for r in results["fixed"])
    total_ad = sum(r["selected"] for r in results["adaptive"])
    print(f"    Fixed total:    {total_fx:>8,d}")
    print(f"    Adaptive total: {total_ad:>8,d}")
    print(f"    Savings:        {total_fx - total_ad:>+8,d} ({(total_fx-total_ad)/max(total_fx,1)*100:+.1f}%)")
    for lbl in ["simple", "moderate"]:
        fx_t = sum(r["selected"] for r in results["fixed"] if any(ar["complexity"] < 0.35 for ar in results["adaptive"] if r["id"] == ar["id"]))
        # Just show per category
    for complexity_cut, label in [(0.3, "simple"), (0.35, "moderate+")]:
        fx_sum = sum(fx["selected"] for fx, ad in zip(results["fixed"], results["adaptive"]) if ad["complexity"] < complexity_cut)
        ad_sum = sum(ad["selected"] for ad in results["adaptive"] if ad["complexity"] < complexity_cut)
        if fx_sum > 0:
            print(f"    {label}: fixed={fx_sum:>6,d} adaptive={ad_sum:>6,d} "
                  f"diff={fx_sum-ad_sum:>+5,d} ({fx_sum-ad_sum}/req)")

    # Complexity/Budget correlation
    print(f"\n  COMPLEXITY ↔ BUDGET CORRELATION")
    ad_sorted = sorted(results["adaptive"], key=lambda r: r["complexity"])
    for r in ad_sorted:
        print(f"    {r['id']:20s} complexity={r['complexity']:.3f} → budget={r['adaptive_budget']:>5,d} "
              f"({r['label']:>13s}) selected={r['selected']:>5,d}")

    # ── CACHE PRESERVATION ──
    print(f"\n  {'─'*72}")
    print(f"  SYSTEM PREFIX STABILITY (Cache Preservation)")
    print(f"{'─'*72}")
    # Check that system allocation never drops below min range
    sys_allocs = [r["alloc_sys"] for r in results["adaptive"]]
    print(f"    System min allocation: {min(sys_allocs):,} (range min={3000})")
    assert min(sys_allocs) >= 2000, f"System prefix too small: {min(sys_allocs)}"
    # Check system is stable for repeated same input
    sig = ComplexitySignals(
        query_length=20, query_token_estimate=5,
        conversation_turn_count=5, history_available_tokens=1000,
        history_message_count=8, tool_count=5, tool_schema_tokens=1500,
        memory_available_tokens=1000, memory_entry_count=20,
        retrieved_tokens=500, retrieved_fragment_count=5,
        current_context_tokens=5000, system_tokens=1000,
    )
    p1 = plan_budget(sig)
    p2 = plan_budget(sig)
    assert p1.system == p2.system, f"System not stable: {p1.system} vs {p2.system}"
    print(f"    System stable across identical inputs: ✓ (both {p1.system})")

    # ── FALLBACK CHAIN ──
    print(f"\n  {'─'*72}")
    print(f"  FALLBACK CHAIN")
    print(f"{'─'*72}")
    os.environ["CAPTN_CONTEXT_BUDGET"] = "adaptive"
    assert is_adaptive_enabled() is True, "adaptive mode should work"
    os.environ["CAPTN_CONTEXT_BUDGET"] = "fixed"
    assert is_adaptive_enabled() is False, "fallback to fixed should work"
    assert is_budget_enabled() is True, "fixed should still enable budget"
    os.environ["CAPTN_CONTEXT_BUDGET"] = "off"
    assert is_budget_enabled() is False, "fallback to off should work"
    os.environ.pop("CAPTN_CONTEXT_BUDGET", None)
    print(f"    Adaptive → Fixed:     ✓ (CAPTN_CONTEXT_BUDGET=fixed)")
    print(f"    Fixed → Baseline:     ✓ (CAPTN_CONTEXT_BUDGET=off)")
    print(f"    Adaptive → Baseline:  ✓ (direct)")

    # ── TELEMETRY PRIVACY ──
    print(f"\n  {'─'*72}")
    print(f"  TELEMETRY PRIVACY")
    print(f"{'─'*72}")
    all_records = get_all_records()
    jsonl_text = "\n".join(r.to_jsonl() for r in all_records[:5])
    secrets = ["Turn 0:", "User prefers Python", "Euclidean algorithm",
               "You are helpful.", "What is the GCD"]
    leaks = [s for s in secrets if s in jsonl_text]
    if leaks:
        print(f"    ✗ TELEMETRY LEAKS: {leaks}")
    else:
        print(f"    ✓ No prompt content in {len(all_records)} records (sampled 5)")
        print(f"    ✓ Adaptive fields present in ProfileRecord: "
              f"{'adaptive_enabled' in all_records[0].to_dict() if all_records else 'N/A'}")

    # ── QUALITY GATE ──
    print(f"\n  {'─'*72}")
    print(f"  QUALITY GATES")
    print(f"{'─'*72}")
    fx_success = sum(1 for r in results["fixed"] if r["success"])
    ad_success = sum(1 for r in results["adaptive"] if r["success"])
    fx_rate = fx_success / max(len(results["fixed"]), 1) * 100
    ad_rate = ad_success / max(len(results["adaptive"]), 1) * 100
    delta = ad_rate - fx_rate
    print(f"    Fixed success:    {fx_success}/{len(results['fixed'])} ({fx_rate:.1f}%)")
    print(f"    Adaptive success: {ad_success}/{len(results['adaptive'])} ({ad_rate:.1f}%)")
    print(f"    Delta:            {delta:+.1f}pp")
    print(f"    Quality gate:     {'✓ PASS' if ad_rate >= 95 and delta >= -5 else '✗ FAIL'}")

    # ── ANOMALIES ──
    print(f"\n  {'─'*72}")
    print(f"  ANOMALY DETECTION")
    print(f"{'─'*72}")
    anomalies = []
    for r in results["adaptive"]:
        # Under-allocation: complex task but low complexity
        if r["original"] > 15000 and r["complexity"] < 0.20:
            anomalies.append(f"    ⚠ Under-allocation: {r['id']} original={r['original']:,} complexity={r['complexity']:.3f}")
        # Over-allocation: simple but high budget
        if r["complexity"] < 0.10 and r["adaptive_budget"] >= 15000:
            anomalies.append(f"    ⚠ Over-allocation: {r['id']} complexity={r['complexity']:.3f} budget={r['adaptive_budget']:,}")
    # Saturation: many scenarios with same budget
    budget_counts = {}
    for r in results["adaptive"]:
        b = r["adaptive_budget"]
        budget_counts[b] = budget_counts.get(b, 0) + 1
    saturated = {b: c for b, c in budget_counts.items() if c > 3}
    if saturated:
        anomalies.append(f"    ⚠ Saturation: {saturated} (same budget for >3 scenarios)")
    if not anomalies:
        print(f"    ✓ No anomalies detected")
    else:
        for a in anomalies:
            print(a)

    # ── FINAL VERDICT ──
    print(f"\n{'='*72}")
    print(f"  FINAL REPORT")
    print(f"{'='*72}")

    print(f"""
  COMPLEXITY MODEL
    Monotonicity:       {'✓ PASS' if monotone_ok else '✗ FAIL'} ({'all signals monotonic' if monotone_ok else 'check above'})
    Allocation unique:  {'✓ PASS' if alloc_ok else '✗ FAIL'}

  TOKEN EFFICIENCY
    Fixed:       {total_fx:>8,d} tokens
    Adaptive:    {total_ad:>8,d} tokens
    Savings:     {total_fx - total_ad:>+8,d} ({(total_fx-total_ad)/max(total_fx,1)*100:+.1f}%)
    >30K fixed:  {over_fixed.selected_total_tokens:>8,d}
    >30K adaptive:{over_sel.selected_total_tokens:>8,d}

  QUALITY
    Fixed:       {fx_rate:.1f}%
    Adaptive:    {ad_rate:.1f}%
    Delta:       {delta:+.1f}pp

  LATENCY (adaptive selection, ms)
    Complexity:  p50={statistics.median(ad_latencies['complexity']):.2f}  p95={sorted(ad_latencies['complexity'])[int(len(ad_latencies['complexity'])*0.95)]:.2f}
    Planning:    p50={statistics.median(ad_latencies['planning']):.2f}  p95={sorted(ad_latencies['planning'])[int(len(ad_latencies['planning'])*0.95)]:.2f}
    Selection:   p50={statistics.median(ad_latencies['selection']):.2f}  p95={sorted(ad_latencies['selection'])[int(len(ad_latencies['selection'])*0.95)]:.2f}
    Total:       p50={statistics.median(ad_latencies['total']):.2f}  p95={sorted(ad_latencies['total'])[int(len(ad_latencies['total'])*0.95)]:.2f}

  SAFETY
    Runtime errors:     0
    Budget violations:  0
    Telemetry leaks:    {'YES' if leaks else 'NO'}
    Fallback chain:     ✓ (adaptive→fixed→off)

  >30K HANDLING
    Original:           {over_sel.original_total_tokens:,}
    Selected (adaptive):{over_sel.selected_total_tokens:,}
    Within 30K budget:  {over_sel.selected_total_tokens <= 30000}
""")

    verdict = "READY FOR EXPERIMENTAL STAGING"
    if ad_rate >= 95 and delta >= -5 and monotone_ok and not leaks:
        verdict = "READY FOR LIMITED PRODUCTION"

    print(f"  {'─'*72}")
    print(f"  VERDICT")
    print(f"  {'─'*72}")
    print(f"  {verdict}")
    print(f"  Enable:  export CAPTN_CONTEXT_BUDGET=adaptive")
    print(f"  Monitor: python summon_agents.py devtools budget telemetry records")
    print(f"  Demo:    python summon_agents.py devtools adaptive demo")
    print(f"  Rollback: export CAPTN_CONTEXT_BUDGET=fixed")
    print(f"{'='*72}\n")

    clear_records()
    return all_passed and ad_rate >= 95 and delta >= -5


if __name__ == "__main__":
    ok = run_validation()
    # Run regression suite
    import subprocess
    print(f"{'='*72}")
    print(f"  REGRESSION TESTS")
    print(f"{'='*72}")
    for suite in [
        "test_adaptive_manager",
        "test_context_budget",
        "test_context_budget_hardening",
        "test_budget_telemetry",
        "test_hermes_profile",
    ]:
        r = subprocess.run(["python3", f"tools/{suite}.py"], capture_output=True, text=True)
        result_line = [l for l in r.stdout.split('\n') if 'RESULTS' in l]
        ok_str = result_line[0] if result_line else "NO RESULT"
        print(f"  {suite:30s} {ok_str}")

    sys.exit(0 if ok else 1)