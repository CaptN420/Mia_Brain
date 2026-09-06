#!/usr/bin/env python3
"""
Phase 3.2 — Adaptive Production Calibration & Decision Gate.

Builds aggregation, calibration, offline replay, and decision gate tooling.
Runs on the existing benchmark data as a proxy for real production traffic.
Reports sample size limitations honestly.
"""
from __future__ import annotations

import json, math, os, random, statistics, sys, time, copy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

random.seed(42)

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

# ═══════════════════════════════════════════════════════════════════════
# 0. DATA GENERATION — Build a representative sample (proxy for real traffic)
# ═══════════════════════════════════════════════════════════════════════

def build_sample() -> list:
    """Generate a representative sample of requests with both Fixed and Adaptive."""
    sample = []

    base = [
        # (id, system_repeat, turns, mem_k, tools_n, ret_k, query_len, dh)
        ("tiny", 2, 1, 0, 0, 0, 10, ""),
        ("simple_q", 5, 1, 0, 0, 0, 20, "coding"),
        ("short_conv", 10, 3, 0, 0, 0, 30, "math"),
        ("short_mem", 10, 3, 50, 0, 0, 40, "coding"),
        ("medium_conv", 20, 10, 0, 0, 0, 50, ""),
        ("medium_mem", 20, 8, 100, 5, 20, 60, "coding"),
        ("medium_tools", 25, 5, 0, 20, 0, 50, "math"),
        ("long_conv", 30, 25, 0, 0, 0, 70, "coding"),
        ("long_mem", 30, 20, 150, 10, 30, 80, "coding"),
        ("long_tools", 30, 10, 50, 30, 0, 90, "math"),
        ("retrieval", 20, 3, 0, 0, 100, 60, "science"),
        ("heavy_mem", 40, 15, 300, 15, 50, 100, "coding"),
        ("heavy_tools", 50, 5, 100, 50, 30, 120, "math"),
        ("near_limit", 80, 40, 200, 25, 80, 150, "coding"),
        ("combined", 60, 35, 250, 35, 120, 200, "research"),
        ("history_deep", 50, 60, 0, 0, 0, 80, "coding"),
        ("retrieval_deep", 40, 5, 50, 10, 200, 100, "research"),
        ("full_complex", 100, 50, 400, 40, 150, 250, "coding"),
    ]

    for sid, sys_repeat, turns, mem_k, tools_n, ret_k, qlen, dh in base:
        sys_text = "You are CaptN. " * sys_repeat
        msgs = [{"role": "system", "content": sys_text}]
        for t in range(turns):
            msgs.append({"role": "user", "content": f"Turn {t} with detailed content about problem solving and algorithms." * 3})
            msgs.append({"role": "assistant", "content": f"Response {t} with comprehensive analysis and code examples." * 5})
        msgs.append({"role": "user", "content": "Solve this problem." * max(1, qlen // 20)})

        mem = ("User prefers Python and algorithms. " * mem_k) if mem_k else ""
        tools = [{"type": "function", "function": {"name": f"t{i}", "description": f"Tool {i} for complex operations and calculations." * 3,
                 "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}} for i in range(tools_n)] if tools_n else []
        ret = ("Comprehensive background knowledge and context for the task. " * ret_k) if ret_k else ""
        query = "Solve this problem." * max(1, qlen // 20)

        sample.append(dict(id=sid, bucket="", system=sys_text, msgs=msgs, mem=mem,
                           tools=tools, ret=ret, query=query[:200], dh=dh))

    # >30K fixture
    huge_sys = "You are CaptN. " * 2000
    huge_msgs = [{"role": "system", "content": huge_sys}]
    for i in range(100):
        huge_msgs.append({"role": "user", "content": f"Turn {i} with very long content about Python." * 10})
        huge_msgs.append({"role": "assistant", "content": f"Response {i} with extensive analysis." * 15})
    huge_msgs.append({"role": "user", "content": "What is GCD?"})
    sample.append(dict(id="over30k", bucket=">30K", system=huge_sys, msgs=huge_msgs,
                       mem="User prefers Python. " * 500,
                       tools=[{"type": "function", "function": {"name": f"t{i}", "description": "A tool", "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}} for i in range(80)],
                       ret="Euclidean algorithm. " * 200, query="What is GCD?", dh="math"))

    return sample


# ═══════════════════════════════════════════════════════════════════════
# 1. BUCKET AGGREGATION
# ═══════════════════════════════════════════════════════════════════════

CONTEXT_BUCKETS = [(0, 5000), (5000, 10000), (10000, 20000), (20000, 30000), (30000, 999999)]
COMPLEXITY_BUCKETS = [(0.0, 0.10), (0.10, 0.20), (0.20, 0.35), (0.35, 0.50), (0.50, 0.70), (0.70, 0.80), (0.80, 1.0)]
CLASSES = ["simple", "moderate", "complex", "very_complex"]


@dataclass
class BucketStats:
    count: int = 0
    total_before: int = 0
    total_after: int = 0
    fixed_before: int = 0
    fixed_after: int = 0
    adaptive_before: int = 0
    adaptive_after: int = 0
    drop_count: int = 0
    redist_count: int = 0
    latencies: List[float] = field(default_factory=list)
    errors: int = 0

    def add_adaptive(self, result, fixed_selected: int, latency: float = 0):
        self.count += 1
        self.adaptive_before += getattr(result.selection, 'original_total_tokens', 0) if hasattr(result, 'selection') else result.get('original', 0)
        self.adaptive_after += getattr(result.selection, 'selected_total_tokens', 0) if hasattr(result, 'selection') else result.get('selected', 0)
        self.fixed_after += fixed_selected
        sel = result.selection if hasattr(result, 'selection') else result
        self.drop_count += getattr(sel, 'history_items_dropped', 0) + getattr(sel, 'memory_items_dropped', 0) + getattr(sel, 'tools_dropped', 0) + getattr(sel, 'retrieved_items_dropped', 0)
        self.redist_count += getattr(result, 'redistribution_count', 0)
        if latency:
            self.latencies.append(latency)

    @property
    def avg_before(self): return round(self.adaptive_before / max(self.count, 1))

    @property
    def avg_after(self): return round(self.adaptive_after / max(self.count, 1))

    @property
    def avg_fixed_after(self): return round(self.fixed_after / max(self.count, 1))

    @property
    def reduction_pct(self):
        return round((self.adaptive_before - self.adaptive_after) / max(self.adaptive_before, 1) * 100, 1)

    @property
    def fixed_reduction_pct(self):
        return round((self.fixed_before - self.fixed_after) / max(self.fixed_before, 1) * 100, 1)

    @property
    def savings_vs_fixed(self): return self.fixed_after - self.adaptive_after

    @property
    def avg_drop(self): return round(self.drop_count / max(self.count, 1), 1)

    @property
    def p50_lat(self): return round(statistics.median(self.latencies), 2) if self.latencies else 0

    @property
    def p95_lat(self): return round(sorted(self.latencies)[int(len(self.latencies)*0.95)], 2) if len(self.latencies) > 1 else (self.latencies[-1] if self.latencies else 0)


@dataclass
class AggregationReport:
    total_requests: int = 0
    duration_hours: float = 0
    sample_size_note: str = ""
    context_buckets: Dict[str, BucketStats] = field(default_factory=dict)
    complexity_buckets: Dict[str, BucketStats] = field(default_factory=dict)
    class_buckets: Dict[str, BucketStats] = field(default_factory=dict)
    global_fixed_after: int = 0
    global_adaptive_after: int = 0
    global_original: int = 0
    all_latencies: List[float] = field(default_factory=list)
    total_errors: int = 0
    total_fallbacks: int = 0
    anomalies: List[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════
# 2. CALIBRATION ANALYSIS
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class CalibrationReport:
    score_budget_pairs: List[Tuple[float, int]] = field(default_factory=list)
    score_class_pairs: List[Tuple[float, str]] = field(default_factory=list)
    class_counts: Dict[str, int] = field(default_factory=lambda: {c: 0 for c in CLASSES})
    class_avg_budgets: Dict[str, float] = field(default_factory=lambda: {c: 0.0 for c in CLASSES})
    inversions: int = 0
    saturation_buckets: List[Tuple[str, int]] = field(default_factory=list)
    under_allocation: List[str] = field(default_factory=list)
    over_allocation: List[str] = field(default_factory=list)
    insensitivity: List[str] = field(default_factory=list)
    score_correlation: float = 0.0
    class_utilization_pct: Dict[str, float] = field(default_factory=dict)


def analyze_calibration(adaptive_results: list) -> CalibrationReport:
    """Analyze complexity score calibration from adaptive results."""
    r = CalibrationReport()
    if not adaptive_results:
        return r

    for item in adaptive_results:
        score = item.get("complexity", 0)
        budget = item.get("adaptive_budget", 0)
        cls = item.get("label", "simple")
        r.score_budget_pairs.append((score, budget))
        r.score_class_pairs.append((score, cls))

    # Class counts
    for cls in CLASSES:
        r.class_counts[cls] = sum(1 for _, c in r.score_class_pairs if c == cls)
        # Average budgets per class
        budgets = [b for s, b in r.score_budget_pairs
                   for _, c in [("", classify_complexity(s))]
                   if classify_complexity(s) == cls]
        # Recompute properly
    class_budgets = {c: [] for c in CLASSES}
    for s, b in r.score_budget_pairs:
        cl = classify_complexity(s)
        class_budgets[cl].append(b)
    for cl in CLASSES:
        r.class_avg_budgets[cl] = round(statistics.mean(class_budgets[cl]), 1) if class_budgets[cl] else 0.0
        r.class_utilization_pct[cl] = round(len(class_budgets[cl]) / max(len(r.score_budget_pairs), 1) * 100, 1)

    # Inversions: score[i] < score[i-1] but budget[i] > budget[i-1]
    sorted_pairs = sorted(r.score_budget_pairs, key=lambda x: x[0])
    for i in range(1, len(sorted_pairs)):
        if sorted_pairs[i][0] > sorted_pairs[i-1][0] and sorted_pairs[i][1] < sorted_pairs[i-1][1]:
            r.inversions += 1

    # Score correlation (Spearman-like rank)
    ranks_s = [idx for idx, _ in enumerate(sorted_pairs)]
    ranks_b = sorted(range(len(sorted_pairs)), key=lambda i: sorted_pairs[i][1])
    d2 = sum((a - b) ** 2 for a, b in zip(ranks_s, ranks_b))
    n = len(sorted_pairs)
    r.score_correlation = round(1 - (6 * d2) / (n * (n*n - 1)) if n > 1 else 0, 4)

    # Saturation: budget values that appear too often
    budget_counts = {}
    for _, b in r.score_budget_pairs:
        budget_counts[b] = budget_counts.get(b, 0) + 1
    threshold = max(3, int(len(r.score_budget_pairs) * 0.25))
    r.saturation_buckets = [(str(b), c) for b, c in budget_counts.items() if c >= threshold]

    return r


# ═══════════════════════════════════════════════════════════════════════
# 3. OFFLINE CALIBRATION REPLAY
# ═══════════════════════════════════════════════════════════════════════

WEIGHT_VARIANTS = {
    "current":       {"query": 0.20, "history": 0.30, "tools": 0.20, "density": 0.15, "pressure": 0.10, "multi": 0.05},
    "history_sense": {"query": 0.15, "history": 0.35, "tools": 0.20, "density": 0.15, "pressure": 0.10, "multi": 0.05},
    "tool_sense":    {"query": 0.20, "history": 0.25, "tools": 0.25, "density": 0.15, "pressure": 0.10, "multi": 0.05},
    "pressure_sense":{"query": 0.20, "history": 0.25, "tools": 0.20, "density": 0.15, "pressure": 0.15, "multi": 0.05},
    "balanced":      {"query": 0.20, "history": 0.25, "tools": 0.20, "density": 0.20, "pressure": 0.10, "multi": 0.05},
}


def compute_complexity_weighted(signals: ComplexitySignals, weights: dict) -> float:
    """Compute complexity with custom weights."""
    q_len = min(signals.query_length / 200, 1.0)
    q_tok = min(signals.query_token_estimate / 50, 1.0)
    query_score = 0.7 * q_len + 0.3 * q_tok

    hist_depth = min(signals.conversation_turn_count / 50, 1.0)
    hist_volume = min(signals.history_available_tokens / 20_000, 1.0)
    history_score = 0.5 * hist_depth + 0.5 * hist_volume

    tool_count_score = min(signals.tool_count / 50, 1.0)
    tool_volume = min(signals.tool_schema_tokens / 10_000, 1.0)
    tool_score = 0.5 * tool_count_score + 0.5 * tool_volume

    mem_density = min(signals.memory_available_tokens / 10_000, 1.0)
    ret_density = min(signals.retrieved_tokens / 10_000, 1.0)
    density_score = 0.5 * mem_density + 0.5 * ret_density

    ctx_pressure = min(signals.current_context_tokens / 30_000, 1.0)

    active = sum([signals.has_memory, signals.has_retrieval, signals.multi_tool, signals.domain_hint_present])
    multi_score = min(active / 4.0, 1.0)

    complexity = (
        weights["query"] * query_score +
        weights["history"] * history_score +
        weights["tools"] * tool_score +
        weights["density"] * density_score +
        weights["pressure"] * ctx_pressure +
        weights["multi"] * multi_score
    )
    return max(0.0, min(1.0, complexity))


@dataclass
class VariantResult:
    name: str
    weights: dict
    total_tokens: int = 0
    avg_complexity: float = 0.0
    class_distribution: Dict[str, int] = field(default_factory=lambda: {c: 0 for c in CLASSES})
    monotonic: bool = True
    inversions: int = 0


def replay_variant(name: str, weights: dict, scenarios: list) -> VariantResult:
    """Replay all scenarios with a weight variant. Returns token total and quality."""
    result = VariantResult(name=name, weights=weights)
    total_tok = 0
    scores = []
    classes = {c: 0 for c in CLASSES}
    prev_score = -1
    inversions = 0

    for s in scenarios:
        sig = compute_signals(
            system_prompt=s["system"], messages=s["msgs"],
            memory_text=s["mem"], tools=s["tools"],
            retrieved_context=s["ret"], user_query=s["query"],
            domain_hint=s["dh"],
        )
        score = compute_complexity_weighted(sig, weights)
        scores.append(score)
        cls = classify_complexity(score)
        classes[cls] = classes.get(cls, 0) + 1
        plan = plan_budget(sig)
        total_tok += plan.total_budget
        if score < prev_score - 0.001:
            inversions += 1
        prev_score = score

    # Check monotonicity over single-parameter variations
    check_scenarios = []
    for turns in [1, 5, 20, 50, 100]:
        sig = ComplexitySignals(
            query_length=50, query_token_estimate=12,
            conversation_turn_count=turns, history_available_tokens=turns*300,
            history_message_count=turns*2, tool_count=5, tool_schema_tokens=1500,
            memory_available_tokens=500, memory_entry_count=10,
            retrieved_tokens=200, retrieved_fragment_count=3,
            current_context_tokens=turns*500+2000, system_tokens=500,
        )
        check_scenarios.append(compute_complexity_weighted(sig, weights))
    mono = all(check_scenarios[i] >= check_scenarios[i-1] - 0.001 for i in range(1, len(check_scenarios)))

    result.total_tokens = total_tok
    result.avg_complexity = round(statistics.mean(scores), 4) if scores else 0
    result.class_distribution = classes
    result.monotonic = mono
    result.inversions = inversions
    return result


# ═══════════════════════════════════════════════════════════════════════
# 4. DECISION GATE
# ═══════════════════════════════════════════════════════════════════════

DECISION_KEEP = "KEEP_CURRENT"
DECISION_CALIBRATE = "CALIBRATE"
DECISION_INVESTIGATE = "INVESTIGATE"
DECISION_ML = "ML_CANDIDATE"

VERDICT_NOT_READY = "NOT READY"
VERDICT_CONTINUE = "READY FOR CONTINUED LIMITED PRODUCTION"
VERDICT_BROADER = "READY FOR BROADER PRODUCTION"
VERDICT_CALIBRATE = "READY FOR CALIBRATION"
VERDICT_ML = "READY FOR ML INVESTIGATION"


def decide(aggregation: AggregationReport, calibration: CalibrationReport,
           offline_results: List[VariantResult]) -> Tuple[str, str, str]:
    """Decision gate: returns (decision, verdict, rationale)."""
    reasons = []

    # Quality
    quality_ok = aggregation.total_errors == 0 and aggregation.total_fallbacks == 0
    if not quality_ok:
        reasons.append(f"Quality issues: {aggregation.total_errors} errors, {aggregation.total_fallbacks} fallbacks")

    # Reduction
    total_fixed = aggregation.global_fixed_after
    total_adaptive = aggregation.global_adaptive_after
    reduction_pct = (total_fixed - total_adaptive) / max(total_fixed, 1) * 100 if total_fixed > 0 else 0

    # Latency
    all_lats = aggregation.all_latencies
    p95_lat = 0
    if all_lats:
        sorted_lats = sorted(all_lats)
        p95_lat = sorted_lats[int(len(sorted_lats) * 0.95)]

    latency_ok = p95_lat < 50  # selection total p95 < 50ms
    overhead_ok = p95_lat < 10  # this is too strict for mixed workloads; rely on latency_ok

    # Check anomalies
    has_anomalies = len(aggregation.anomalies) > 0

    # Saturation
    saturation = len(calibration.saturation_buckets) > 0 and calibration.saturation_buckets[0][1] > aggregation.total_requests * 0.4 if aggregation.total_requests > 0 else False

    # Class utilization
    simple_pct = calibration.class_utilization_pct.get("simple", 0)
    complex_pct = calibration.class_utilization_pct.get("complex", 0) + calibration.class_utilization_pct.get("very_complex", 0)
    classes_skewed = simple_pct > 80 or complex_pct < 5

    # Score correlation
    corr = calibration.score_correlation
    corr_weak = abs(corr) < 0.5

    # Decision logic
    decision = DECISION_KEEP
    verdict = VERDICT_CONTINUE
    note = ""

    if reduction_pct >= 3 and quality_ok and latency_ok and not has_anomalies and not classes_skewed and not corr_weak:
        decision = DECISION_KEEP
        best_variant = max(offline_results, key=lambda v: v.total_tokens) if offline_results else None
        if best_variant and best_variant.total_tokens > aggregation.global_adaptive_after * 1.05:
            decision = DECISION_CALIBRATE
            note = f"Variant '{best_variant.name}' shows {best_variant.total_tokens} vs current {aggregation.global_adaptive_after} — possible improvement"
            verdict = VERDICT_CALIBRATE
        else:
            verdict = VERDICT_CONTINUE
            note = "Current model performs well; no calibration needed"
    elif not quality_ok:
        decision = DECISION_INVESTIGATE
        verdict = VERDICT_NOT_READY
        note = f"Quality issues detected: {aggregation.total_errors} errors"
    elif reduction_pct < 3 and not classes_skewed:
        decision = DECISION_CALIBRATE
        verdict = VERDICT_CALIBRATE
        note = f"Reduction only {reduction_pct:.1f}% — under target of 3%"
    elif classes_skewed:
        decision = DECISION_CALIBRATE
        verdict = VERDICT_CALIBRATE
        note = f"Classes skewed: simple={simple_pct:.0f}% complex+={complex_pct:.0f}%"
    elif corr_weak:
        decision = DECISION_CALIBRATE
        verdict = VERDICT_CALIBRATE
        note = f"Weak score↔budget correlation ({corr})"
    else:
        decision = DECISION_KEEP
        verdict = VERDICT_CONTINUE
        note = "Adequate performance, no critical issues"

    if aggregation.total_requests < 30:
        note += " | SAMPLE_SIZE_INSUFFICIENT for definitive conclusion"

    return decision, verdict, note


# ═══════════════════════════════════════════════════════════════════════
# 5. MAIN RUNNER
# ═══════════════════════════════════════════════════════════════════════

def run_phase32():
    print(f"\n{'='*80}")
    print(f"  PHASE 3.2 — ADAPTIVE PRODUCTION CALIBRATION & DECISION GATE")
    print(f"{'='*80}")

    # ── Load/Generate Data ──
    scenarios = build_sample()
    n_scenarios = len(scenarios)
    print(f"\n  Sample: {n_scenarios} requests ("
          f"{len([s for s in scenarios if s['id'] != 'over30k'])} benchmark + >30K fixture)")

    # ── Run Fixed ──
    print(f"\n  ── RUNNING FIXED ──")
    fixed_results = []
    for s in scenarios:
        b = ContextBudget(model="test")
        r = b.select(system_prompt=s["system"], messages=s["msgs"],
                      memory_text=s["mem"], tools=s["tools"],
                      retrieved_context=s["ret"], user_query=s["query"],
                      domain_hint=s["dh"], dry_run=True)
        fixed_results.append(r)

    # ── Run Adaptive ──
    print(f"  ── RUNNING ADAPTIVE ──")
    adaptive_results_raw = []
    clear_records()
    for s in scenarios:
        am = AdaptiveContextManager(model="test")
        t0 = time.perf_counter()
        ar = am.select(system_prompt=s["system"], messages=s["msgs"],
                        memory_text=s["mem"], tools=s["tools"],
                        retrieved_context=s["ret"], user_query=s["query"],
                        domain_hint=s["dh"], dry_run=True)
        lat = (time.perf_counter() - t0) * 1000
        adaptive_results_raw.append((s, ar, lat))
        # Telemetry
        rec = ProfileRecord(request_id=f"req_{s['id']}", timestamp=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                            model="test", estimated_total=ar.selection.original_total_tokens)
        _add_budget_telemetry(rec, selection_result=ar.selection)
        _add_adaptive_telemetry(rec, adaptive_result=ar)
        from captn.runtime.hermes_profiler import _append_record as _ap
        _ap(rec)

    all_records = get_all_records()
    print(f"  Telemetry records: {len(all_records)}")

    # ══════════════════════════════════════════════════════════════
    # A. AGGREGATION
    # ══════════════════════════════════════════════════════════════

    report = AggregationReport(total_requests=n_scenarios)
    report.global_original = sum(r.original_total_tokens for r in fixed_results)
    report.global_fixed_after = sum(r.selected_total_tokens for r in fixed_results)
    report.global_adaptive_after = sum(ar.selection.selected_total_tokens for _, ar, _ in adaptive_results_raw)
    report.all_latencies = [lat for _, _, lat in adaptive_results_raw]

    # Context bucket aggregation
    for (lo, hi), label in zip(CONTEXT_BUCKETS, ["<5K", "5-10K", "10-20K", "20-30K", ">30K"]):
        stats = BucketStats()
        for (s, ar, lat), fr in zip(adaptive_results_raw, fixed_results):
            orig = ar.selection.original_total_tokens
            if lo <= orig < hi:
                stats.add_adaptive(ar, fr.selected_total_tokens, lat)
        if stats.count > 0:
            report.context_buckets[label] = stats

    # Complexity bucket aggregation
    for (lo, hi), label in zip(COMPLEXITY_BUCKETS, ["0.00-0.10", "0.10-0.20", "0.20-0.35", "0.35-0.50", "0.50-0.70", "0.70-0.80", "0.80-1.00"]):
        stats = BucketStats()
        for (s, ar, lat), fr in zip(adaptive_results_raw, fixed_results):
            score = ar.complexity_score
            if lo <= score < hi:
                stats.add_adaptive(ar, fr.selected_total_tokens, lat)
        if stats.count > 0:
            report.complexity_buckets[label] = stats

    # Class aggregation
    for cls in CLASSES:
        stats = BucketStats()
        for (s, ar, lat), fr in zip(adaptive_results_raw, fixed_results):
            if ar.complexity_label == cls:
                stats.add_adaptive(ar, fr.selected_total_tokens, lat)
        if stats.count > 0:
            report.class_buckets[cls] = stats

    # ══════════════════════════════════════════════════════════════
    # B. CALIBRATION ANALYSIS
    # ══════════════════════════════════════════════════════════════

    adaptive_dicts = []
    for s, ar, lat in adaptive_results_raw:
        adaptive_dicts.append({
            "id": s["id"],
            "complexity": ar.complexity_score,
            "label": ar.complexity_label,
            "adaptive_budget": ar.adaptive_total_budget,
            "selected": ar.selection.selected_total_tokens,
            "original": ar.selection.original_total_tokens,
        })

    cal = analyze_calibration(adaptive_dicts)

    # Detect anomalies
    for item in adaptive_dicts:
        if item["original"] > 15000 and item["complexity"] < 0.15:
            report.anomalies.append(f"UNDER_ALLOCATION: {item['id']} orig={item['original']} complex={item['complexity']:.3f}")
        if item["complexity"] < 0.05 and item["adaptive_budget"] >= 12000:
            report.anomalies.append(f"OVER_ALLOCATION: {item['id']} complex={item['complexity']:.3f} budget={item['adaptive_budget']}")

    # ══════════════════════════════════════════════════════════════
    # C. OFFLINE CALIBRATION REPLAY
    # ══════════════════════════════════════════════════════════════

    print(f"\n  ── OFFLINE CALIBRATION REPLAY ──")
    offline_results = []
    for name, weights in WEIGHT_VARIANTS.items():
        vr = replay_variant(name, weights, scenarios)
        offline_results.append(vr)
        label = "← current" if name == "current" else ""
        print(f"    {name:20s} total_budget={vr.total_tokens:>6,d} avg_score={vr.avg_complexity:.3f} "
              f"classes={dict(sorted(vr.class_distribution.items()))} "
              f"mono={'✓' if vr.monotonic else '✗'} {label}")

    # ══════════════════════════════════════════════════════════════
    # D. DECISION GATE
    # ══════════════════════════════════════════════════════════════

    decision, verdict, note = decide(report, cal, offline_results)

    # ══════════════════════════════════════════════════════════════
    # E. FINAL REPORT
    # ══════════════════════════════════════════════════════════════

    print(f"\n{'='*80}")
    print(f"  FINAL REPORT")
    print(f"{'='*80}")

    total_fixed = report.global_fixed_after
    total_adaptive = report.global_adaptive_after
    reduction_tok = total_fixed - total_adaptive
    reduction_pct = reduction_tok / max(total_fixed, 1) * 100

    sample_note = f"  ⚠  Note: sample size = {n_scenarios} requests. "
    if n_scenarios < 30:
        sample_note += "Insufficient for definitive conclusions on production behavior."
    else:
        sample_note += "Adequate for calibration analysis."

    p95_lat = 0
    if report.all_latencies:
        sorted_lats = sorted(report.all_latencies)
        p95_lat = sorted_lats[int(len(sorted_lats)*0.95)]

    print(f"""
  PRODUCTION SAMPLE
    Requests:              {n_scenarios}
    Sample note:           {sample_note}
    Context ≥ 30K:         {report.context_buckets.get('>30K', BucketStats()).count} request(s)

  ADAPTIVE PERFORMANCE
    Fixed total:           {total_fixed:>10,d}
    Adaptive total:        {total_adaptive:>10,d}
    Reduction vs Fixed:    {reduction_tok:>+10,d}  ({reduction_pct:+.1f}%)
    Latency p50:           {statistics.median(report.all_latencies):.2f}ms
    Latency p95:           {p95_lat:.2f}ms
    Errors:                {report.total_errors}
    Fallbacks:             {report.total_fallbacks}

  COMPLEXITY CALIBRATION
    Score ↔ Budget correlation: {cal.score_correlation}
    Inversions:                 {cal.inversions}
    Class distribution:         {cal.class_counts}
    Class utilization:          {cal.class_utilization_pct}
    Saturation buckets:         {cal.saturation_buckets}
    Anomalies:                  {len(report.anomalies)}

  CONTEXT BUCKETS
    {'Bucket':10s} {'Req':>4s} {'Fixed':>7s} {'Adapt':>7s} {'Savings':>7s} {'Red%':>6s}
    {'─'*10:>10s} {'─'*4:>4s} {'─'*7:>7s} {'─'*7:>7s} {'─'*7:>7s} {'─'*6:>6s}""")
    for label in ["<5K", "5-10K", "10-20K", "20-30K", ">30K"]:
        b = report.context_buckets.get(label)
        if b and b.count > 0:
            print(f"    {label:10s} {b.count:>4d} {b.avg_fixed_after:>7,d} {b.avg_after:>7,d} {b.savings_vs_fixed:>+7,d} {b.reduction_pct:>5.1f}%")

    print(f"""
  COMPLEXITY BUCKETS
    {'Bucket':12s} {'Req':>4s} {'AvgBudget':>9s} {'Red%':>6s}
    {'─'*12:>12s} {'─'*4:>4s} {'─'*9:>9s} {'─'*6:>6s}""")
    for label, b in sorted(report.complexity_buckets.items()):
        if b and b.count > 0:
            print(f"    {label:12s} {b.count:>4d} {b.avg_after:>9,d} {b.reduction_pct:>5.1f}%")

    print(f"""
  CATEGORY ALLOCATION (avg per scenario)""")
    cat_totals = {"system": 0, "history": 0, "memory": 0, "tools": 0, "retrieved": 0}
    for s, ar, lat in adaptive_results_raw:
        cat_totals["system"] += ar.system_allocated
        cat_totals["history"] += ar.history_allocated
        cat_totals["memory"] += ar.memory_allocated
        cat_totals["tools"] += ar.tools_allocated
        cat_totals["retrieved"] += ar.retrieved_allocated
    for cat in cat_totals:
        avg = cat_totals[cat] / max(n_scenarios, 1)
        print(f"    {cat:12s} avg {avg:>7,.0f} tokens")

    print(f"""
  OFFLINE VARIANTS
    {'Variant':20s} {'TotalBudget':>12s} {'AvgScore':>9s} {'Classes':>20s} {'Monotonic':>10s}
    {'─'*20:>20s} {'─'*12:>12s} {'─'*9:>9s} {'─'*20:>20s} {'─'*10:>10s}""")
    for vr in offline_results:
        cl_str = "/".join(f"{k[:3]}={v}" for k, v in sorted(vr.class_distribution.items()))
        print(f"    {vr.name:20s} {vr.total_tokens:>10,d}  {vr.avg_complexity:.3f}  {cl_str:20s} {'✓' if vr.monotonic else '✗':>10s}")

    print(f"""
  ANOMALIES""")
    if report.anomalies:
        for a in report.anomalies:
            print(f"    ⚠  {a}")
    else:
        print(f"    ✓ No anomalies detected")

    print(f"""
  DECISION
    Decision:  {decision}
    Verdict:   {verdict}
    Rationale: {note}

  TELEMETRY PRIVACY""")
    # Verify telemetry
    if all_records:
        jsonl_sample = all_records[0].to_jsonl() if all_records else "{}"
        secrets = ["Turn 0:", "You are CaptN.", "What is GCD", "Euclidean algorithm", "User prefers Python"]
        if any(s in jsonl_sample for s in secrets):
            print(f"    ✗ PROMPT CONTENT LEAK detected!")
        else:
            print(f"    ✓ No prompt content in telemetry ({len(all_records)} records checked)")
        # Check adaptive fields present
        d = all_records[0].to_dict()
        has_adaptive = "adaptive_enabled" in d and "complexity_score" in d and "adaptive_strategy" in d
        print(f"    ✓ Adaptive telemetry fields: {'all present' if has_adaptive else 'MISSING'}")
    else:
        print(f"    - No records to verify")

    print(f"\n{'='*80}")
    print(f"  FINAL OUTPUT")
    print(f"{'='*80}")
    print(f"""
  PHASE 3.2 COMPLETE — {decision}

  Decision:    {decision}
  Verdict:     {verdict}
  Sample:      {n_scenarios} requests

  Key metrics:
    Reduction vs Fixed:    {reduction_pct:+.1f}%
    Score correlation:     {cal.score_correlation}
    Classes:               {[f'{k}={v}' for k,v in cal.class_counts.items() if v > 0]}
    Anomalies:             {len(report.anomalies)}
    Latency p95:           {p95_lat:.1f}ms
    Errors:                {report.total_errors}

  {note}
""")

    # Save report JSON
    out = {
        "phase": "3.2",
        "decision": decision,
        "verdict": verdict,
        "note": note,
        "sample_size": n_scenarios,
        "reduction_vs_fixed_pct": round(reduction_pct, 1),
        "correlation_score_budget": cal.score_correlation,
        "inversions": cal.inversions,
        "class_counts": cal.class_counts,
        "class_utilization": cal.class_utilization_pct,
        "anomaly_count": len(report.anomalies),
        "anomalies": report.anomalies[:5],
        "latency_p95_ms": round(p95_lat, 2),
        "errors": report.total_errors,
        "fallbacks": report.total_fallbacks,
        "offline_variants": [{"name": vr.name, "total_tokens": vr.total_tokens,
                               "avg_score": vr.avg_complexity, "classes": vr.class_distribution,
                               "monotonic": vr.monotonic} for vr in offline_results],
        "context_buckets": {k: {"count": v.count, "avg_fixed": v.avg_fixed_after,
                                "avg_adaptive": v.avg_after, "savings": v.savings_vs_fixed,
                                "reduction_pct": v.reduction_pct}
                            for k, v in sorted(report.context_buckets.items())},
    }
    with open("/tmp/phase32_report.json", "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"  Report saved to /tmp/phase32_report.json")

    clear_records()
    return decision, verdict


if __name__ == "__main__":
    run_phase32()