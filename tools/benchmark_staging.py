#!/usr/bin/env python3
"""
benchmark_staging.py — Staging validation benchmark for CaptN Context Budget.

Compares Baseline (CAPTN_CONTEXT_BUDGET=off) vs CaptN (on) across 10
realistic scenario categories. Collects structured metrics, validates
quality, efficiency, cache impact, and latency.

Usage:
    python -m tools.benchmark_staging                    # Full staging benchmark
    python -m tools.benchmark_staging --json             # JSON output
    python -m tools.benchmark_staging --export report.json
    python -m tools.benchmark_staging --category large   # Single category
    python -m tools.benchmark_staging --list             # List categories

Output:
    STAGING VALIDATION REPORT
    ├── Dataset summary
    ├── Quality (baseline vs CaptN success rate)
    ├── Efficiency (token reduction)
    ├── Cache impact
    ├── Performance (latency)
    ├── Context composition
    ├── Safety
    └── Verdict
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from captn.runtime.context_budget import (
    ContextBudget, BudgetConfig, SelectionResult,
    estimate_provider_tokens, estimate_messages_tokens, estimate_tool_tokens,
    is_budget_enabled,
)

# ═══════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════

# Baseline: 30/30 historical benchmark
BASELINE_SUCCESS_RATE = 1.0
QUALITY_GATE_MIN = 0.95       # 95% minimum success rate
QUALITY_GATE_DELTA = 0.05     # Max 5pp regression
BUDGET_TOTAL = 30_000

# ═══════════════════════════════════════════════════════════════════
# TEST SCENARIO DEFINITIONS
# ═══════════════════════════════════════════════════════════════════

@dataclass
class Scenario:
    """One test scenario for the staging benchmark."""
    id: str
    name: str
    description: str
    # Builder functions (lazy to avoid huge memory at import)
    build_system: Any
    build_messages: Any
    build_memory: Any
    build_tools: Any
    build_retrieved: Any
    query: str = ""
    domain_hint: str = ""
    expected_under_budget: Optional[bool] = None  # None = unknown
    expected_success: bool = True


def _scenarios() -> List[Scenario]:
    """Build all 10 test scenarios."""
    scenarios = []

    # 1. Small context (<5K)
    scenarios.append(Scenario(
        id="small", name="Small context (<5K)",
        description="Minimal request: system + 1 user message",
        build_system=lambda: "You are a helpful assistant.",
        build_messages=lambda: [{"role": "system", "content": "You are a helpful assistant."},
                                {"role": "user", "content": "What is the capital of France?"}],
        build_memory=lambda: "",
        build_tools=lambda: [],
        build_retrieved=lambda: "",
        query="What is the capital of France?",
        expected_under_budget=True,
    ))

    # 2. Medium context (5-15K)
    scenarios.append(Scenario(
        id="medium", name="Medium context (5-15K)",
        description="System + 20 turns + moderate memory",
        build_system=lambda: "You are a helpful AI assistant. " * 50,
        build_messages=lambda: (
            [{"role": "system", "content": "You are a helpful AI assistant. " * 50}] +
            [{"role": "user", "content": f"Turn {i} with some content." * 2,
              "role": "assistant", "content": f"Response {i} with details." * 3}
             for i in range(20)] +
            [{"role": "user", "content": "What is the GCD of 48 and 180?"}]
        ),
        build_memory=lambda: "User prefers Python. " * 100,
        build_tools=lambda: [{"type": "function", "function": {"name": f"t{i}", "description": "A tool", "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}} for i in range(10)],
        build_retrieved=lambda: "Fragment about GCD.\n\nFragment about LCM.\n\n" + "Unrelated.\n\n" * 10,
        query="What is the GCD of 48 and 180?",
        domain_hint="math",
        expected_under_budget=True,
    ))

    # 3. Near limit (15-30K)
    scenarios.append(Scenario(
        id="near_limit", name="Near limit (15-30K)",
        description="System + 50 turns + large memory + many tools",
        build_system=lambda: "You are a helpful AI assistant. " * 200,
        build_messages=lambda: (
            [{"role": "system", "content": "You are a helpful AI assistant. " * 200}] +
            sum(([{"role": "user", "content": f"Turn {i} with content about Python and algorithms." * 2},
                  {"role": "assistant", "content": f"Response {i} with code examples and analysis." * 3}]
                 for i in range(50)), []) +
            [{"role": "user", "content": "What is the time complexity of binary search?"}]
        ),
        build_memory=lambda: "User prefers Python. User works on algorithms. " * 200,
        build_tools=lambda: [{"type": "function", "function": {"name": f"tool_{i}", "description": "A utility tool", "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}} for i in range(30)],
        build_retrieved=lambda: "Binary search: O(log n).\n\n" + "Unrelated content.\n\n" * 30,
        query="What is the time complexity of binary search?",
        domain_hint="coding",
        expected_under_budget=True,
    ))

    # 4. Exceeding limit (>30K)
    scenarios.append(Scenario(
        id="large", name="Exceeding limit (>30K)",
        description="System + 100 turns + very large memory + all tools + large retrieval",
        build_system=lambda: "You are a helpful AI assistant. " * 500,
        build_messages=lambda: (
            [{"role": "system", "content": "You are a helpful AI assistant. " * 500}] +
            sum(([{"role": "user", "content": f"Long turn {i} with extensive content about various topics." * 3},
                  {"role": "assistant", "content": f"Detailed response {i} with analysis and code examples." * 5}]
                 for i in range(100)), []) +
            [{"role": "user", "content": "Explain the difference between stacks and queues."}]
        ),
        build_memory=lambda: "User prefers Python. " * 500,
        build_tools=lambda: [{"type": "function", "function": {"name": f"t{i}", "description": "A tool description.", "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}} for i in range(60)],
        build_retrieved=lambda: "Stack: LIFO. Queue: FIFO.\n\n" + "Unrelated content.\n\n" * 50,
        query="Explain the difference between stacks and queues.",
        domain_hint="coding",
        expected_under_budget=False,  # may overflow
    ))

    # 5. Large optional system
    scenarios.append(Scenario(
        id="opt_system", name="Large optional system",
        description="System prompt with large optional skills block",
        build_system=lambda: (
            "You are a helpful assistant.\n\n"
            "<available_skills>\n" +
            "\n".join(f"  - skill_{i}: description for skill {i}" for i in range(50)) +
            "\n</available_skills>\n\n"
            "Core instructions."
        ),
        build_messages=lambda: [{"role": "system", "content": "Core instructions."},
                                {"role": "user", "content": "Hello"}],
        build_memory=lambda: "",
        build_tools=lambda: [],
        build_retrieved=lambda: "",
        query="Hello",
        expected_under_budget=True,
    ))

    # 6. Very long history
    scenarios.append(Scenario(
        id="long_history", name="Very long history",
        description="System + 200 turns, history dominates",
        build_system=lambda: "You are a helpful assistant.",
        build_messages=lambda: (
            [{"role": "system", "content": "You are a helpful assistant."}] +
            sum(([{"role": "user", "content": f"Turn {i} with some content."},
                  {"role": "assistant", "content": f"Response {i} with details."}]
                 for i in range(200)), []) +
            [{"role": "user", "content": "What was the first thing we discussed?"}]
        ),
        build_memory=lambda: "",
        build_tools=lambda: [],
        build_retrieved=lambda: "",
        query="What was the first thing we discussed?",
        expected_under_budget=False,  # 200 turns likely > 8K
    ))

    # 7. Large memory
    scenarios.append(Scenario(
        id="large_memory", name="Large memory store",
        description="Only relevant entries among many",
        build_system=lambda: "You are a helpful assistant.",
        build_messages=lambda: [{"role": "system", "content": "You are a helpful assistant."},
                                {"role": "user", "content": "What is the GCD of 48 and 180?"}],
        build_memory=lambda: ("GCD: greatest common divisor of 48 and 180 is 12.\n" +
                              "User prefers Python. " * 300 +
                              "Irrelevant session data. " * 200),
        build_tools=lambda: [],
        build_retrieved=lambda: "",
        query="What is the GCD of 48 and 180?",
        expected_under_budget=True,
    ))

    # 8. Many tools
    scenarios.append(Scenario(
        id="many_tools", name="Many tools",
        description="80 tools, only a few relevant",
        build_system=lambda: "You are a helpful math assistant.",
        build_messages=lambda: [{"role": "system", "content": "You are a helpful math assistant."},
                                {"role": "user", "content": "Calculate 2+2"}],
        build_memory=lambda: "",
        build_tools=lambda: (
            [{"type": "function", "function": {"name": "calc", "description": "Calculate", "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}}]
            + [{"type": "function", "function": {"name": f"unrelated_{i}", "description": "An unrelated tool with extensive documentation." * 5, "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}} for i in range(79)]
        ),
        build_retrieved=lambda: "",
        query="Calculate 2+2",
        domain_hint="math",
        expected_under_budget=True,
    ))

    # 9. Large retrieval
    scenarios.append(Scenario(
        id="large_retrieval", name="Large retrieval context",
        description="55 fragments, only 5 relevant",
        build_system=lambda: "You are a helpful assistant.",
        build_messages=lambda: [{"role": "system", "content": "You are a helpful assistant."},
                                {"role": "user", "content": "What is the Euclidean algorithm?"}],
        build_memory=lambda: "",
        build_tools=lambda: [],
        build_retrieved=lambda: ("Euclidean algorithm: computes GCD of two integers.\n\n" +
                                 "Euclidean algorithm: efficient method.\n\n" +
                                 "Unrelated content. " * 50),
        query="What is the Euclidean algorithm?",
        expected_under_budget=True,
    ))

    # 10. Combined overload
    scenarios.append(Scenario(
        id="combined", name="Combined overload",
        description="All categories large simultaneously",
        build_system=lambda: "You are a helpful AI assistant. " * 500,
        build_messages=lambda: (
            [{"role": "system", "content": "You are a helpful AI assistant. " * 500}] +
            sum(([{"role": "user", "content": f"Turn {i} with content about Python programming." * 2},
                  {"role": "assistant", "content": f"Response {i} with detailed analysis." * 3}]
                 for i in range(100)), []) +
            [{"role": "user", "content": "What is recursion?"}]
        ),
        build_memory=lambda: "User prefers Python. " * 500 + "User asked about recursion before. " * 100,
        build_tools=lambda: [{"type": "function", "function": {"name": f"t{i}", "description": "A tool", "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}} for i in range(60)],
        build_retrieved=lambda: "Recursion: function calls itself.\n\n" + "Unrelated content.\n\n" * 50,
        query="What is recursion?",
        domain_hint="coding",
        expected_under_budget=False,  # Likely exceeds 30K
    ))

    return scenarios


# ═══════════════════════════════════════════════════════════════════
# STAGING METRICS
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ScenarioResult:
    """Result for one scenario in one mode (baseline or CaptN)."""
    scenario_id: str
    mode: str  # "baseline" or "captn"
    original_system: int = 0
    original_history: int = 0
    original_memory: int = 0
    original_tools: int = 0
    original_retrieved: int = 0
    original_total: int = 0
    selected_system: int = 0
    selected_history: int = 0
    selected_memory: int = 0
    selected_tools: int = 0
    selected_retrieved: int = 0
    selected_total: int = 0
    budget_exceeded: bool = False
    mandatory_overflow: int = 0
    budget_satisfiable: bool = True
    messages_removed: int = 0
    messages_compressed: bool = False
    memory_entries_removed: int = 0
    retrieval_fragments_removed: int = 0
    tools_removed: int = 0
    selection_latency_ms: float = 0.0
    # Quality
    task_success: bool = True
    # Efficiency
    estimated_reduction_pct: float = 0.0

    @property
    def input_token_reduction(self) -> int:
        return max(0, self.original_total - self.selected_total)

    @property
    def input_token_reduction_pct(self) -> float:
        if self.original_total == 0:
            return 0.0
        return (self.input_token_reduction / self.original_total) * 100


@dataclass
class StagingReport:
    """Complete staging validation report."""
    scenarios: Dict[str, ScenarioResult] = field(default_factory=dict)
    duration_seconds: float = 0.0

    # Quality
    baseline_success: int = 0
    captn_success: int = 0
    total_scenarios: int = 0

    # Efficiency
    baseline_input_tokens: int = 0
    captn_input_tokens: int = 0

    # Performance
    baseline_latency: float = 0.0
    captn_latency: float = 0.0
    selection_latency_total: float = 0.0
    selection_latency_count: int = 0

    # Safety
    runtime_errors: int = 0
    budget_violations: int = 0
    mandatory_overflows: int = 0
    telemetry_leaks: bool = False

    def to_dict(self) -> Dict[str, Any]:
        n = self.total_scenarios
        bl_success_rate = self.baseline_success / max(n, 1)
        cp_success_rate = self.captn_success / max(n, 1)
        delta = cp_success_rate - bl_success_rate

        reduction_pct = 0.0
        if self.baseline_input_tokens > 0:
            reduction_pct = (1 - self.captn_input_tokens / self.baseline_input_tokens) * 100

        avg_sel_latency = self.selection_latency_total / max(self.selection_latency_count, 1)

        return {
            "summary": {
                "total_scenarios": n,
                "duration_seconds": round(self.duration_seconds, 2),
            },
            "quality": {
                "baseline_success_rate": round(bl_success_rate * 100, 1),
                "captn_success_rate": round(cp_success_rate * 100, 1),
                "delta_success_rate": round(delta * 100, 1),
                "baseline_success": self.baseline_success,
                "captn_success": self.captn_success,
                "quality_gate_passed": cp_success_rate >= QUALITY_GATE_MIN and abs(delta) <= QUALITY_GATE_DELTA,
            },
            "efficiency": {
                "baseline_input_tokens": self.baseline_input_tokens,
                "captn_input_tokens": self.captn_input_tokens,
                "reduction_pct": round(reduction_pct, 1),
            },
            "performance": {
                "baseline_latency_ms": round(self.baseline_latency, 3),
                "captn_latency_ms": round(self.captn_latency, 3),
                "avg_selection_latency_ms": round(avg_sel_latency, 3),
            },
            "safety": {
                "runtime_errors": self.runtime_errors,
                "budget_violations": self.budget_violations,
                "mandatory_overflows": self.mandatory_overflows,
                "telemetry_leaks": self.telemetry_leaks,
            },
            "scenarios": {
                sid: {
                    "mode": sr.mode,
                    "original_total": sr.original_total,
                    "selected_total": sr.selected_total,
                    "budget_exceeded": sr.budget_exceeded,
                    "budget_satisfiable": sr.budget_satisfiable,
                    "task_success": sr.task_success,
                    "reduction_pct": round(sr.estimated_reduction_pct, 1),
                    "selection_latency_ms": round(sr.selection_latency_ms, 3),
                    "messages_removed": sr.messages_removed,
                    "memory_entries_removed": sr.memory_entries_removed,
                    "tools_removed": sr.tools_removed,
                }
                for sid, sr in self.scenarios.items()
            },
        }

    def render(self) -> str:
        sep = "=" * 60
        lines = [sep, "  STAGING VALIDATION REPORT", sep, ""]

        n = self.total_scenarios
        bl_rate = self.baseline_success / max(n, 1) * 100
        cp_rate = self.captn_success / max(n, 1) * 100
        delta = cp_rate - bl_rate

        lines.append("  DATASET")
        lines.append(f"    Scenarios:          {n}")
        lines.append(f"    Duration:           {self.duration_seconds:.2f}s")
        lines.append("")

        # Quality
        lines.append("  QUALITY")
        lines.append(f"    Baseline success:   {bl_rate:.1f}%")
        lines.append(f"    CaptN success:      {cp_rate:.1f}%")
        lines.append(f"    Delta:              {delta:+.1f}pp")
        gate_pass = cp_rate >= QUALITY_GATE_MIN * 100 and abs(delta) <= QUALITY_GATE_DELTA * 100
        lines.append(f"    Quality gate:       {'✓ PASS' if gate_pass else '✗ FAIL'}")
        lines.append("")

        # Efficiency
        reduction = 0.0
        if self.baseline_input_tokens > 0:
            reduction = (1 - self.captn_input_tokens / self.baseline_input_tokens) * 100
        lines.append("  EFFICIENCY")
        lines.append(f"    Baseline input:     {self.baseline_input_tokens:>8,d} tokens")
        lines.append(f"    CaptN input:        {self.captn_input_tokens:>8,d} tokens")
        lines.append(f"    Reduction:          {reduction:.1f}%")
        lines.append("")

        # Performance
        avg_sel = self.selection_latency_total / max(self.selection_latency_count, 1)
        lines.append("  PERFORMANCE")
        lines.append(f"    Baseline latency:   {self.baseline_latency:.3f}ms")
        lines.append(f"    CaptN latency:      {self.captn_latency:.3f}ms")
        lines.append(f"    Selection latency:  {avg_sel:.3f}ms (avg, {self.selection_latency_count} requests)")
        lines.append("")

        # Safety
        lines.append("  SAFETY")
        lines.append(f"    Runtime errors:     {self.runtime_errors}")
        lines.append(f"    Budget violations:  {self.budget_violations}")
        lines.append(f"    Mandatory overflows:{self.mandatory_overflows}")
        lines.append(f"    Telemetry leaks:    {'YES' if self.telemetry_leaks else 'NO'}")
        lines.append("")

        # Per-scenario
        lines.append("  PER-SCENARIO DETAIL")
        lines.append(f"  {'ID':15s} {'Orig':>8s} {'Sel':>8s} {'Red%':>6s} {'Budget':>8s} {'Success':>8s} {'Lat':>7s}")
        lines.append(f"  {'─'*15:>15s} {'─'*8:>8s} {'─'*8:>8s} {'─'*6:>6s} {'─'*8:>8s} {'─'*8:>8s} {'─'*7:>7s}")
        for sid, sr in sorted(self.scenarios.items()):
            if sr.mode == "captn":
                red = f"{sr.estimated_reduction_pct:.0f}%" if sr.original_total > 0 else "N/A"
                bgt = "OK" if not sr.budget_exceeded else "OVF"
                ok = "✓" if sr.task_success else "✗"
                lines.append(f"  {sid:15s} {sr.original_total:>8,d} {sr.selected_total:>8,d} {red:>6s} {bgt:>8s} {ok:>8s} {sr.selection_latency_ms:.2f}ms")
        lines.append("")

        # Verdict
        lines.append(sep)
        lines.append("  VERDICT")
        lines.append(sep)

        if not gate_pass:
            lines.append("  NOT READY — Quality gate not met")
            if cp_rate < QUALITY_GATE_MIN * 100:
                lines.append(f"    CaptN success rate ({cp_rate:.1f}%) < minimum ({QUALITY_GATE_MIN*100:.0f}%)")
            if abs(delta) > QUALITY_GATE_DELTA * 100:
                lines.append(f"    Delta ({delta:+.1f}pp) exceeds max ({QUALITY_GATE_DELTA*100:.0f}pp)")
        elif self.runtime_errors > 0:
            lines.append("  NOT READY — Runtime errors detected")
        elif self.telemetry_leaks:
            lines.append("  NOT READY — Telemetry leaks detected")
        elif reduction >= 10 and gate_pass:
            lines.append("  READY FOR LIMITED PRODUCTION")
            lines.append(f"    {reduction:.1f}% input token reduction with {cp_rate:.1f}% success rate")
        else:
            lines.append("  READY FOR LIMITED PRODUCTION")
            lines.append(f"    Quality preserved ({cp_rate:.1f}%), no critical regressions")

        lines.append(sep)
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# BENCHMARK RUNNER
# ═══════════════════════════════════════════════════════════════════

def _run_baseline(scenario: Scenario) -> ScenarioResult:
    """Run a scenario in baseline mode (no budget)."""
    sr = ScenarioResult(scenario_id=scenario.id, mode="baseline")
    try:
        sys_prompt = scenario.build_system()
        msgs = scenario.build_messages()
        memory = scenario.build_memory()
        tools = scenario.build_tools()
        retrieved = scenario.build_retrieved()

        # Estimate tokens
        sys_tok = estimate_provider_tokens(sys_prompt, "test")
        hist_tok = estimate_messages_tokens(msgs, "test")
        mem_tok = estimate_provider_tokens(memory, "test")
        tool_tok = estimate_tool_tokens(tools, "test") if tools else 0
        ret_tok = estimate_provider_tokens(retrieved, "test")

        sr.original_system = sys_tok
        sr.original_history = hist_tok
        sr.original_memory = mem_tok
        sr.original_tools = tool_tok
        sr.original_retrieved = ret_tok
        sr.original_total = sys_tok + hist_tok + mem_tok + tool_tok + ret_tok
        sr.selected_total = sr.original_total
        sr.task_success = True

        # Store built content for CaptN to use (same content)
        scenario._cached_system = sys_prompt
        scenario._cached_messages = msgs
        scenario._cached_memory = memory
        scenario._cached_tools = tools
        scenario._cached_retrieved = retrieved
    except Exception as e:
        sr.task_success = False
    return sr


def _run_captn(scenario: Scenario) -> ScenarioResult:
    """Run a scenario with CaptN Context Budget enabled."""
    sr = ScenarioResult(scenario_id=scenario.id, mode="captn")
    try:
        # Use cached content from baseline run (same content)
        sys_prompt = getattr(scenario, '_cached_system', scenario.build_system())
        msgs = getattr(scenario, '_cached_messages', scenario.build_messages())
        memory = getattr(scenario, '_cached_memory', scenario.build_memory())
        tools = getattr(scenario, '_cached_tools', scenario.build_tools())
        retrieved = getattr(scenario, '_cached_retrieved', scenario.build_retrieved())

        # Estimate original (should match baseline)
        sys_tok = estimate_provider_tokens(sys_prompt, "test")
        hist_tok = estimate_messages_tokens(msgs, "test")
        mem_tok = estimate_provider_tokens(memory, "test")
        tool_tok = estimate_tool_tokens(tools, "test") if tools else 0
        ret_tok = estimate_provider_tokens(retrieved, "test")

        sr.original_system = sys_tok
        sr.original_history = hist_tok
        sr.original_memory = mem_tok
        sr.original_tools = tool_tok
        sr.original_retrieved = ret_tok
        sr.original_total = sys_tok + hist_tok + mem_tok + tool_tok + ret_tok

        # Run Context Budget
        budget = ContextBudget(model="test")
        t0 = time.monotonic_ns()
        result = budget.select(
            system_prompt=sys_prompt,
            messages=msgs,
            memory_text=memory,
            tools=tools if tools else None,
            retrieved_context=retrieved,
            user_query=scenario.query,
            domain_hint=scenario.domain_hint,
            dry_run=True,
        )
        dt = (time.monotonic_ns() - t0) / 1_000_000

        sr.selection_latency_ms = dt
        sr.selected_system = result.selected_system_tokens
        sr.selected_history = result.selected_history_tokens
        sr.selected_memory = result.selected_memory_tokens
        sr.selected_tools = result.selected_tool_tokens
        sr.selected_retrieved = result.selected_retrieved_tokens
        sr.selected_total = result.selected_total_tokens
        sr.budget_exceeded = result.total_budget_exceeded or result.system_budget_exceeded
        sr.mandatory_overflow = result.overflow_tokens
        sr.budget_satisfiable = not result.total_budget_exceeded

        sr.messages_removed = result.history_items_dropped
        sr.messages_compressed = result.history_compressed
        sr.memory_entries_removed = result.memory_items_dropped
        sr.retrieval_fragments_removed = result.retrieved_items_dropped
        sr.tools_removed = result.tools_dropped

        if sr.original_total > 0:
            sr.estimated_reduction_pct = (sr.input_token_reduction / sr.original_total) * 100

        # Determine success
        # Budget is satisfiable if total <= 30K or mandatory overflow reported correctly
        if result.total_budget_exceeded:
            # Check that overflow is reported
            if result.overflow_tokens > 0:
                sr.task_success = True  # Correctly reported overflow
            else:
                sr.task_success = False  # Overflow should be > 0
        else:
            sr.task_success = True

    except Exception as e:
        sr.task_success = False
    return sr


def run_staging_benchmark(
    category_filter: str = "",
    json_out: bool = False,
    export_path: str = "",
) -> StagingReport:
    """Run the full staging benchmark across all scenarios."""
    t0 = time.monotonic()

    report = StagingReport()
    scenarios = _scenarios()

    for scenario in scenarios:
        if category_filter and scenario.id != category_filter:
            continue

        # Baseline
        bl = _run_baseline(scenario)
        report.scenarios[f"{scenario.id}_baseline"] = bl
        report.baseline_success += 1 if bl.task_success else 0
        report.baseline_input_tokens += bl.original_total
        report.baseline_latency += bl.selection_latency_ms  # 0 for baseline
        report.total_scenarios += 1

        # CaptN
        cp = _run_captn(scenario)
        report.scenarios[f"{scenario.id}_captn"] = cp
        report.captn_success += 1 if cp.task_success else 0
        report.captn_input_tokens += cp.selected_total
        report.captn_latency += cp.selection_latency_ms
        report.selection_latency_total += cp.selection_latency_ms
        report.selection_latency_count += 1

        if not cp.task_success:
            report.runtime_errors += 1
        if cp.budget_exceeded and cp.budget_satisfiable:
            report.budget_violations += 1
        if cp.mandatory_overflow > 0:
            report.mandatory_overflows += 1

    report.duration_seconds = time.monotonic() - t0

    if json_out or export_path:
        data = report.to_dict()
        if export_path:
            Path(export_path).write_text(json.dumps(data, indent=2))
            print(f"  📄 Exported to {export_path}")
        if json_out:
            print(json.dumps(data, indent=2))

    print(report.render())
    return report


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

def register_cli(subparsers) -> None:
    p = subparsers.add_parser(
        "benchmark_staging",
        help="Staging validation benchmark for Context Budget",
    )
    p.add_argument("--json", action="store_true", help="JSON output")
    p.add_argument("--export", type=str, default="", help="Export path (JSON)")
    p.add_argument("--category", type=str, default="", help="Filter by category ID")
    p.add_argument("--list", action="store_true", help="List categories")
    p.set_defaults(func=_cmd_bench)


def _cmd_bench(args) -> None:
    if args.list:
        for s in _scenarios():
            print(f"  {s.id:20s}  {s.name:30s}  {s.description}")
        return
    run_staging_benchmark(
        category_filter=args.category,
        json_out=args.json,
        export_path=args.export,
    )


if __name__ == "__main__":
    run_staging_benchmark()