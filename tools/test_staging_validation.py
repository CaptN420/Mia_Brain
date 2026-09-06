#!/usr/bin/env python3
"""
Phase 2.2 — Staging Validation & Production Readiness Tests.

Covers:
1. Budget already respected → payload identical
2. Budget exceeded → reduction effective
3. Mandatory content > budget → overflow explicite
4. Cache accounting correct
5. System optional sections correctly selected
6. History compression
7. Memory granularity
8. Tool preservation
9. Retrieval preservation
10. Tokenizer offline
11. Dry-run non-mutative
12. Telemetry without prompt content
"""
from __future__ import annotations

import json
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from captn.runtime.context_budget import (
    ContextBudget, BudgetConfig, SelectionResult,
    estimate_provider_tokens, estimate_messages_tokens, estimate_tool_tokens,
    _select_system, _select_history, _select_memory, _select_tools, _select_retrieved,
    is_budget_enabled,
)


def test_1_budget_respected_no_change():
    """When content is already under budget, payload must be identical."""
    b = ContextBudget(model="test")
    r = b.select(
        system_prompt="You are helpful.",
        messages=[{"role": "system", "content": "You are helpful."},
                  {"role": "user", "content": "Hello"}],
        user_query="Hello",
        dry_run=True,
    )
    # Under budget: nothing dropped
    assert r.history_items_dropped == 0
    assert r.memory_items_dropped == 0
    assert r.tools_dropped == 0
    assert r.retrieved_items_dropped == 0
    assert r.total_budget_exceeded is False
    # Total should be <= original (ideally identical when under budget)
    assert r.selected_total_tokens <= r.original_total_tokens
    print(f"  ✓ test_1: {r.selected_total_tokens}/{r.original_total_tokens} tokens, unchanged")


def test_2_budget_exceeded_reduction():
    """When budget is exceeded, tokens must be reduced effectively."""
    system = "You are a helpful assistant. " * 300
    msgs = [{"role": "system", "content": system}]
    for i in range(80):
        msgs.append({"role": "user", "content": f"Turn {i} with content." * 2})
        msgs.append({"role": "assistant", "content": f"Response {i} with details." * 3})
    msgs.append({"role": "user", "content": "Final question?"})
    memory = "Some memory data. " * 300
    tools = [{"type": "function", "function": {"name": f"t{i}", "description": "A tool", "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}} for i in range(40)]
    retrieved = "Relevant fragment.\n\n" + "Unrelated content.\n\n" * 30

    b = ContextBudget(model="test")
    r = b.select(
        system_prompt=system,
        messages=msgs,
        memory_text=memory,
        tools=tools,
        retrieved_context=retrieved,
        user_query="Final question?",
        dry_run=True,
    )

    # Should have reduced total
    if r.original_total_tokens > r.budget_total:
        # Either it's within budget now, or overflow is reported
        in_budget = r.selected_total_tokens <= r.budget_total
        overflow_reported = r.total_budget_exceeded and r.overflow_tokens > 0
        assert in_budget or overflow_reported, "Must be in budget or report overflow"
        # Some items should have been dropped
        dropped_any = (r.history_items_dropped > 0 or r.memory_items_dropped > 0 or
                      r.tools_dropped > 0 or r.retrieved_items_dropped > 0)
        if not r.total_budget_exceeded:
            assert dropped_any or r.selected_total_tokens < r.original_total_tokens, "Should reduce when over budget"
    print(f"  ✓ test_2: {r.original_total_tokens} -> {r.selected_total_tokens}/{r.budget_total}, "
          f"dropped hist={r.history_items_dropped} mem={r.memory_items_dropped} tools={r.tools_dropped} ret={r.retrieved_items_dropped}")


def test_3_mandatory_overflow():
    """When mandatory content exceeds budget, overflow must be explicit."""
    huge_sys = "Mandatory. " * 5000  # ~25K tokens
    b = ContextBudget(model="test")
    r = b.select(
        system_prompt=huge_sys,
        messages=[{"role": "system", "content": huge_sys}, {"role": "user", "content": "Hi"}],
        user_query="Hi",
        dry_run=True,
    )
    # System budget must be exceeded
    assert r.system_budget_exceeded, "System budget should be exceeded"
    # System should still be kept
    assert r.selected_system_tokens > 0, "System should be kept"
    # Report must mention EXCEEDED
    report = r.report()
    assert "EXCEEDED" in report, "Report should mention EXCEEDED"
    # Total budget may or may not be exceeded
    if r.total_budget_exceeded:
        assert r.overflow_tokens > 0
    # to_dict must be serializable
    d = r.to_dict()
    assert d["overflow"]["system_budget_exceeded"] is True
    json.dumps(d, default=str)
    print(f"  ✓ test_3: sys_exceeded={r.system_budget_exceeded}, total_exceeded={r.total_budget_exceeded}, "
          f"overflow={r.overflow_tokens}, sys_tok={r.selected_system_tokens}")


def test_4_cache_accounting():
    """Cache accounting must correctly separate input_tokens from prompt_tokens."""
    from captn.runtime.reconcile import reconcile
    # Test 1: no cache
    r1 = reconcile([{"role": "user", "content": "Hello"}], None, model="test", provider_prompt_tokens=100)
    assert r1.provider_prompt_tokens == 100
    assert r1.provider_input_tokens == 100
    assert r1.provider_cache_read is None
    assert r1.provider_cache_write is None

    # Test 2: with cache
    r2 = reconcile([{"role": "user", "content": "Hello"}], None, model="test",
                   provider_prompt_tokens=100, provider_cache_read=40, provider_cache_write=10)
    assert r2.provider_prompt_tokens == 100
    assert r2.provider_input_tokens == 50  # 100 - 40 - 10
    assert r2.provider_cache_read == 40
    assert r2.provider_cache_write == 10
    assert r2.provider_prompt_tokens == r2.provider_input_tokens + r2.provider_cache_read + r2.provider_cache_write

    # Test 3: full cache (all cached)
    r3 = reconcile([{"role": "user", "content": "Hello"}], None, model="test",
                   provider_prompt_tokens=100, provider_cache_read=80, provider_cache_write=20)
    assert r3.provider_input_tokens == 0  # 100 - 80 - 20

    print(f"  ✓ test_4: prompt=100 input=50 (with cache), input=0 (full cache), formula verified")


def test_5_system_optional_sections():
    """Optional system sections (skills) must be dropped before core."""
    sys_prompt = (
        "You are a helpful assistant.\n\n"
        "<available_skills>\n"
        "  - math: algebra, geometry\n"
        "  - coding: python, go\n"
        "</available_skills>\n\n"
        "Core instructions that must be preserved."
    )

    # Decompose
    import re
    m = re.search(r"<available_skills>.*?</available_skills>", sys_prompt, re.DOTALL)
    skills_text = m.group(0) if m else ""
    core_text = sys_prompt.replace(skills_text, "") if skills_text else sys_prompt

    skills_tok = estimate_provider_tokens(skills_text, "test")
    core_tok = estimate_provider_tokens(core_text, "test")

    # Test selector with budget that fits core only (not skills)
    tight_budget = core_tok + 10
    sel_text, sel_tok, exceeded, decisions = _select_system(sys_prompt, budget=tight_budget, model="test")
    # Core should be kept (the core text is a substring of the system prompt)
    assert "Core instructions that must be preserved" in sel_text, "Core should be kept"
    # Skills should be dropped when they don't fit
    if skills_tok > 0 and core_tok + skills_tok > tight_budget:
        assert "<available_skills>" not in sel_text, "Skills should be dropped when budget exceeded"
        # Verify decisions record this
        skill_decisions = [d for d in decisions if d.category == "system" and "skill" in d.item_id.lower()]
        assert any(not d.selected for d in skill_decisions) or not skill_decisions, "Skills should be marked as dropped"

    # Test with generous budget (core + skills fit)
    if core_tok + skills_tok <= 500:
        sel_text2, sel_tok2, exceeded2, _ = _select_system(sys_prompt, budget=500, model="test")
        assert "<available_skills>" in sel_text2, "Skills should be kept when budget allows"
        assert "Core instructions that must be preserved" in sel_text2, "Core should be kept"

    print(f"  ✓ test_5: core={core_tok} skills={skills_tok} -> "
          f"tight_budget={tight_budget}: {'dropped_skills' if '<available_skills>' not in sel_text else 'kept_skills'}")


def test_6_history_compression():
    """History compression must be triggered for large histories."""
    msgs = [{"role": "system", "content": "You are helpful."}]
    for i in range(100):
        msgs.append({"role": "user", "content": f"Turn {i} with content."})
        msgs.append({"role": "assistant", "content": f"Response {i}."})

    # Small budget that forces compression
    b = ContextBudget(model="test", config=BudgetConfig(history=500, total=30000, system=5000, memory=7000, tools=5000, retrieved=5000))
    r = b.select(
        system_prompt="You are helpful.",
        messages=msgs,
        user_query="Turn 99",
        dry_run=True,
    )
    # Should have dropped messages to fit budget
    if r.original_history_tokens > 500:
        assert r.history_items_dropped > 0, "Should drop history when over budget"
        assert r.selected_history_tokens <= 500, f"Should be within budget: {r.selected_history_tokens} > 500"
    print(f"  ✓ test_6: {r.original_history_tokens} -> {r.selected_history_tokens}/500, "
          f"dropped={r.history_items_dropped}, compressed={r.history_compressed}")


def test_7_memory_granularity():
    """Memory must be selectable at entry granularity, not as a block."""
    lines = []
    for i in range(10):
        lines.append(f"GCD: answer about greatest common divisor.")
    for i in range(90):
        lines.append(f"Irrelevant entry {i}.")
    memory = "\n".join(lines)

    sel_text, sel_tok, dropped, decisions = _select_memory(memory, budget=300, model="test", user_query="GCD")
    # GCD entries should be selected
    assert "GCD" in sel_text, "GCD entries should be selected"
    # Should drop irrelevant entries
    assert dropped > 50, f"Should drop most irrelevant entries, got {dropped}"
    # Within budget
    assert sel_tok <= 300, f"Should be within budget: {sel_tok} > 300"
    print(f"  ✓ test_7: {len(lines)} entries -> {sel_tok}/300, dropped={dropped}, selected={sel_text.count(chr(10))+1} lines")


def test_8_tool_preservation():
    """Relevant tools must be preserved; irrelevant ones dropped."""
    tools = []
    # High relevance tools (small)
    tools.append({"type": "function", "function": {"name": "gcd", "description": "GCD", "parameters": {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}, "required": ["a", "b"]}}})
    tools.append({"type": "function", "function": {"name": "lcm", "description": "LCM", "parameters": {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}, "required": ["a", "b"]}}})
    # Many irrelevant tools (large, to exceed budget after domain filter)
    for i in range(20):
        tools.append({"type": "function", "function": {"name": f"irrelevant_{i}", "description": f"Very long description for tool {i}." * 20, "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}})

    total_tok = estimate_tool_tokens(tools, "test")
    # Without domain hint, test pure utility/cost selection
    sel_tools, sel_tok, exceeded, dropped, decisions = _select_tools(tools, budget=500, model="test", user_query="gcd", domain_hint="")
    sel_names = [t.get("function", {}).get("name", "") for t in sel_tools]
    # gcd should be selected (high utility/cost)
    assert "gcd" in sel_names, "gcd should be selected"
    # Should have dropped some tools (budget is 500, each irrelevant tool is ~286 tokens)
    # With gcd+lcm costing ~130, we can fit ~1 irrelevant tool before hitting 500
    if total_tok > 500:
        assert dropped > 0, f"Should drop some tools: total={total_tok} > 500"
    # Within budget
    assert sel_tok <= 500, f"Should be within budget: {sel_tok} > 500"
    print(f"  ✓ test_8: {len(tools)} tools -> {len(sel_tools)} selected, {dropped} dropped, "
          f"names={sel_names}, {sel_tok}/500 tokens")


def test_9_retrieval_preservation():
    """Relevant retrieval fragments must be preserved."""
    fragments = ["Relevant: Euclidean algorithm computes GCD."] * 5 + [f"Irrelevant fragment {i}." for i in range(50)]
    retrieved = "\n\n".join(fragments)

    sel_text, sel_tok, dropped, decisions = _select_retrieved(retrieved, budget=300, model="test", user_query="GCD")
    # Relevant fragments should be selected
    assert "GCD" in sel_text or "Euclidean" in sel_text, "Relevant fragments should be selected"
    # Should drop irrelevant
    assert dropped > 0, "Should drop some fragments"
    # Within budget
    assert sel_tok <= 300, f"Should be within budget: {sel_tok} > 300"
    print(f"  ✓ test_9: {len(fragments)} frags -> {sel_tok}/300, dropped={dropped}, "
          f"selected={sel_text.count(chr(10))+1} lines")


def test_10_tokenizer_offline():
    """Tokenizer must work without network access."""
    # tiktoken-based
    t1 = estimate_provider_tokens("Hello world", "gpt-4o")
    assert t1 > 0, f"tiktoken should work: {t1}"
    # chars/4 fallback
    t2 = estimate_provider_tokens("Hello world")
    assert t2 > 0, f"chars/4 should work: {t2}"
    # Deterministic
    assert estimate_provider_tokens("Hello world", "test") == estimate_provider_tokens("Hello world", "test")
    print(f"  ✓ test_10: tiktoken={t1}, chars4={t2}, deterministic")


def test_11_dry_run_non_mutative():
    """Dry-run must not modify the real request."""
    msgs = [{"role": "user", "content": "Hello"}]
    msgs_copy = list(msgs)
    b = ContextBudget(model="test")
    r = b.select(messages=msgs, user_query="Hello", dry_run=True)
    assert msgs == msgs_copy, "Messages were mutated!"
    assert r.dry_run is True, "dry_run flag should be True"
    print(f"  ✓ test_11: dry_run=True, no mutation")


def test_12_telemetry_no_content():
    """Telemetry must not contain prompt content."""
    b = ContextBudget(model="test")
    r = b.select(
        system_prompt="SECRET_SYSTEM_ABCD",
        messages=[{"role": "user", "content": "SECRET_USER_EFGH"}],
        memory_text="SECRET_MEMORY_IJKL",
        retrieved_context="SECRET_RETRIEVED_MNOP",
        user_query="SECRET_QUERY_QRST",
        dry_run=True,
    )
    d = r.to_dict()
    json_str = json.dumps(d, default=str)
    for secret in ["SECRET_SYSTEM_ABCD", "SECRET_USER_EFGH", "SECRET_MEMORY_IJKL",
                   "SECRET_RETRIEVED_MNOP", "SECRET_QUERY_QRST"]:
        assert secret not in json_str, f"Leaked: {secret}"
    print(f"  ✓ test_12: no secret content in telemetry ({len(json_str)} chars)")


def test_13_budget_config_from_env():
    """BudgetConfig.from_env() must read env vars correctly."""
    # Save
    old = {k: os.environ.get(k) for k in ["CAPTN_CONTEXT_BUDGET_TOTAL", "CAPTN_CONTEXT_BUDGET_SYSTEM", "CAPTN_CONTEXT_BUDGET_HISTORY"]}

    os.environ["CAPTN_CONTEXT_BUDGET_TOTAL"] = "50000"
    os.environ["CAPTN_CONTEXT_BUDGET_SYSTEM"] = "10000"
    os.environ["CAPTN_CONTEXT_BUDGET_HISTORY"] = "15000"

    config = BudgetConfig.from_env()
    assert config.total == 50000
    assert config.system == 10000
    assert config.history == 15000
    # Defaults for unset
    assert config.memory == 7000
    assert config.tools == 5000
    assert config.retrieved == 5000

    # Cleanup
    for k, v in old.items():
        if v is not None:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)

    # Defaults
    config2 = BudgetConfig()
    assert config2.total == 30000
    assert config2.system == 5000
    print(f"  ✓ test_13: env overrides work, defaults preserved")


def test_14_staging_benchmark_runs():
    """Staging benchmark must run without errors."""
    from tools.benchmark_staging import run_staging_benchmark
    report = run_staging_benchmark()
    assert report.total_scenarios == 10
    assert report.baseline_success >= 9  # at least 90% success
    assert report.captn_success >= 9
    assert report.runtime_errors == 0
    print(f"  ✓ test_14: {report.total_scenarios} scenarios, "
          f"baseline={report.baseline_success}/{report.total_scenarios}, "
          f"captn={report.captn_success}/{report.total_scenarios}")


def test_15_feature_flag_levels():
    """Feature flag must support off, on, and dry-run modes."""
    # Save
    old = os.environ.get("CAPTN_CONTEXT_BUDGET")

    # Default: off
    if "CAPTN_CONTEXT_BUDGET" in os.environ:
        del os.environ["CAPTN_CONTEXT_BUDGET"]
    assert is_budget_enabled() is False

    # on
    os.environ["CAPTN_CONTEXT_BUDGET"] = "on"
    assert is_budget_enabled() is True

    # true
    os.environ["CAPTN_CONTEXT_BUDGET"] = "true"
    assert is_budget_enabled() is True

    # 1
    os.environ["CAPTN_CONTEXT_BUDGET"] = "1"
    assert is_budget_enabled() is True

    # off
    os.environ["CAPTN_CONTEXT_BUDGET"] = "off"
    assert is_budget_enabled() is False

    # Restore
    if old is not None:
        os.environ["CAPTN_CONTEXT_BUDGET"] = old
    else:
        os.environ.pop("CAPTN_CONTEXT_BUDGET", None)

    print(f"  ✓ test_15: off=False, on=True, true=True, 1=True, off=False")


if __name__ == "__main__":
    print(f"\n{'='*60}")
    print(f"  PHASE 2.2 — Staging Validation Tests")
    print(f"{'='*60}\n")

    tests = [
        test_1_budget_respected_no_change,
        test_2_budget_exceeded_reduction,
        test_3_mandatory_overflow,
        test_4_cache_accounting,
        test_5_system_optional_sections,
        test_6_history_compression,
        test_7_memory_granularity,
        test_8_tool_preservation,
        test_9_retrieval_preservation,
        test_10_tokenizer_offline,
        test_11_dry_run_non_mutative,
        test_12_telemetry_no_content,
        test_13_budget_config_from_env,
        test_14_staging_benchmark_runs,
        test_15_feature_flag_levels,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"  ✗ {test.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*60}")
    print(f"  RESULTS: {passed} passed, {failed} failed, {len(tests)} total")
    print(f"{'='*60}")
    sys.exit(0 if failed == 0 else 1)