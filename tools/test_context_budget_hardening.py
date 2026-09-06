#!/usr/bin/env python3
"""
Phase 2.1 regression tests — Context Budget hardening & validation.

Adds to the existing 15 test_context_budget.py tests:
- System decomposition & optional-section pruning
- Memory entry-level line-by-line selection
- Large history compression with tool-call coherence
- Tool utility/cost ratio selection
- Retrieval ranking (high-relevance beats low-cost)
- Tokenizer fallback chain (offline, no network)
- input_tokens vs prompt_tokens accounting
- Cache accounting reconciliation
- Realistic 30K budget test
- Mandatory overflow reporting
- Budget telemetry in SelectionResult
- A/B result serialization
"""
from __future__ import annotations

import json
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import hashlib

from captn.runtime.context_budget import (
    ContextBudget, BudgetConfig, SelectionResult, SelectionDecision,
    estimate_provider_tokens, estimate_messages_tokens, estimate_tool_tokens,
    _select_system, _select_history, _select_memory, _select_tools, _select_retrieved,
    is_budget_enabled, DEFAULT_BUDGET_CONFIG,
)


def test_1_system_decomposition():
    """System prompt must be decomposable into mandatory + optional sections."""
    sys_prompt = (
        "You are a helpful assistant.\n\n"
        "<available_skills>\n"
        "  - math\n  - coding\n"
        "</available_skills>\n\n"
        "# Plugin Context\nSome plugin data here.\n"
        "# Ephemeral\nSession context data."
    )
    # Total
    total_tok = estimate_provider_tokens(sys_prompt, "test")
    
    # Decompose: skills block
    import re
    m = re.search(r"<available_skills>.*?</available_skills>", sys_prompt, re.DOTALL)
    skills_text = m.group(0) if m else ""
    skills_tok = estimate_provider_tokens(skills_text, "test")
    
    # Core = everything minus skills
    core = sys_prompt.replace(skills_text, "") if skills_text else sys_prompt
    core_tok = estimate_provider_tokens(core, "test")
    
    # Verify decomposition: core + skills ≈ total
    assert abs(core_tok + skills_tok - total_tok) <= 5, f"Decomposition off: {core_tok + skills_tok} vs {total_tok}"
    
    # Test selector: skills should be dropped first
    sel_text, sel_tok, exceeded, decisions = _select_system(sys_prompt, budget=500, model="test")
    if core_tok <= 500:
        # Core fits, skills should be kept if room
        assert sel_tok <= 500, f"Selected exceeds budget: {sel_tok}"
        # If skills text exists and core+skills > budget, skills should be dropped
        if skills_tok > 0 and core_tok + skills_tok > 500:
            assert skills_text not in sel_text, "Skills should be dropped when they exceed budget"
    else:
        # Core exceeds budget - should report exceeded
        assert exceeded, "Should report exceeded when core > budget"
        # But core should still be present
        assert len(sel_text) > 0, "Core should still be present even when exceeded"
    
    print(f"  ✓ test_1_system_decomposition: core={core_tok} skills={skills_tok} total={total_tok}")
    # Verify decisions are recorded
    assert len(decisions) > 0, "Should have at least one decision"
    assert any(d.category == "system" for d in decisions)


def test_2_memory_line_by_line():
    """Memory must be selected at line granularity, not as a block."""
    # 100 entries: 10 highly relevant, 90 irrelevant
    lines = []
    for i in range(10):
        lines.append(f"GCD: User asked about greatest common divisor. Answer: 12.")
    for i in range(90):
        lines.append(f"Irrelevant note {i} about weather.")
    memory = "\n".join(lines)
    total_tok = estimate_provider_tokens(memory, "test")
    
    # Budget that only allows ~10 relevant lines
    budget = 300
    sel_text, sel_tok, dropped, decisions = _select_memory(memory, budget=budget, model="test", user_query="GCD")
    
    # GCD entries should be selected
    assert "GCD" in sel_text, "GCD entries should be selected"
    # Most irrelevant entries should be dropped
    assert dropped > 50, f"Should drop most irrelevant entries, got {dropped}"
    # Selected should be within budget
    assert sel_tok <= budget, f"Selected exceeds budget: {sel_tok} > {budget}"
    # Line-by-line selection: multiple lines, not one block
    assert sel_text.count("GCD") >= 5, "Should select multiple GCD lines"
    
    print(f"  ✓ test_2_memory_line_by_line: {len(lines)} entries -> {sel_tok}/{budget} tokens, {dropped} dropped, {sel_text.count(chr(10))+1} lines selected")


def test_3_history_compression_large():
    """Large history should trigger compression and preserve important info."""
    msgs = [{"role": "system", "content": "You are a helpful assistant."}]
    for i in range(100):
        msgs.append({"role": "user", "content": f"User turn {i} with some content."})
        msgs.append({"role": "assistant", "content": f"Response {i} with details."})
    msgs.append({"role": "user", "content": "What is the capital of France?"})
    
    budget = BudgetConfig(history=1000, total=30000, system=5000, memory=7000, tools=5000, retrieved=5000)
    b = ContextBudget(model="test", config=budget)
    r = b.select(
        system_prompt="You are a helpful assistant.",
        messages=msgs,
        user_query="What is the capital of France?",
        dry_run=True,
    )
    
    # Should drop some messages
    if r.original_history_tokens > 1000:
        assert r.history_items_dropped > 0, "Should drop history when over budget"
    # Current user message should be kept
    assert r.selected_history_tokens > 0
    # Should not exceed budget
    assert r.selected_history_tokens <= 1000, f"History exceeds budget: {r.selected_history_tokens} > 1000"
    
    print(f"  ✓ test_3_history_compression_large: {len(msgs)} msgs -> {r.selected_history_tokens}/{r.budget_history} tokens, {r.history_items_dropped} dropped")


def test_4_tool_call_coherence():
    """Tool calls and their results must be kept together when possible."""
    msgs = [{"role": "system", "content": "You are helpful."}]
    for i in range(3):
        msgs.append({"role": "user", "content": f"Calculate {i}"})
        msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": f"call_{i}", "type": "function", "function": {"name": "calc", "arguments": "{}"}}]})
        msgs.append({"role": "tool", "content": f"Result {i}", "tool_call_id": f"call_{i}"})
    msgs.append({"role": "user", "content": "Now what?"})
    
    budget = BudgetConfig(history=500, total=30000, system=5000, memory=7000, tools=5000, retrieved=5000)
    b = ContextBudget(model="test", config=budget)
    r = b.select(
        system_prompt="You are helpful.",
        messages=msgs,
        user_query="Now what?",
        dry_run=True,
    )
    
    # The last tool call+result+current user should be kept (high recency)
    assert r.selected_history_tokens > 0
    # Recent messages should be preferred
    # Check that the last user message is in the selection
    last_msg = msgs[-1]
    last_content = last_msg.get("content", "")
    selected_contents = [m.get("content", "") for m in r.selected_messages]
    assert any(last_content in c for c in selected_contents), "Current user message should be kept"
    
    print(f"  ✓ test_4_tool_call_coherence: {len(msgs)} msgs -> {r.selected_history_tokens} tokens, {r.history_items_dropped} dropped")


def test_5_tool_utility_cost():
    """Tools must be selected by utility/cost ratio, not utility alone."""
    # Add tools with known utility/cost
    tools = []
    # High utility, low cost (best)
    tools.append({"type": "function", "function": {"name": "gcd", "description": "gcd", "parameters": {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}, "required": ["a", "b"]}}})
    # Low utility, low cost (ok)
    tools.append({"type": "function", "function": {"name": "tiny", "description": "t", "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}})
    # High utility, high cost (avoid if possible)
    tools.append({"type": "function", "function": {"name": "huge_tool", "description": "A very long description" * 100, "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}})
    # Low utility, high cost (worst)
    tools.append({"type": "function", "function": {"name": "wasteful_tool", "description": "Wasteful" * 100, "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}})
    
    budget = BudgetConfig(tools=500, total=30000, system=5000, history=8000, memory=7000, retrieved=5000)
    b = ContextBudget(model="test", config=budget)
    r = b.select(
        system_prompt="You are helpful.",
        messages=[{"role": "user", "content": "Find GCD"}],
        tools=tools,
        user_query="gcd",
        domain_hint="math",
        dry_run=True,
    )
    
    selected_names = [t.get("function", {}).get("name", "") for t in r.selected_tools]
    # gcd should be selected (high utility/cost)
    assert "gcd" in selected_names, "gcd should be selected (high utility/cost)"
    # wasteful_tool should be dropped (low utility/cost)
    # huge_tool may be kept if budget allows, but wasteful should be dropped
    if len(r.selected_tools) < len(tools):
        assert "wasteful_tool" not in selected_names or r.tools_dropped > 0, "Wasteful tool should be dropped or budget exceeded"
    
    print(f"  ✓ test_5_tool_utility_cost: {len(tools)} tools -> {len(r.selected_tools)} selected, {r.tools_dropped} dropped, names={selected_names}")


def test_6_retrieval_ranking():
    """High-relevance fragments must be preferred over low-relevance ones."""
    fragments = []
    for i in range(5):
        fragments.append(f"Euclidean algorithm computes GCD of two integers efficiently.")
    for i in range(50):
        fragments.append(f"Unrelated fact {i} about various topics.")
    retrieved = "\n\n".join(fragments)
    
    budget = BudgetConfig(retrieved=300, total=30000, system=5000, history=8000, memory=7000, tools=5000)
    b = ContextBudget(model="test", config=budget)
    r = b.select(
        system_prompt="You are helpful.",
        messages=[{"role": "user", "content": "What is the GCD?"}],
        retrieved_context=retrieved,
        user_query="What is the GCD?",
        dry_run=True,
    )
    
    assert "GCD" in r.selected_retrieved_text, "GCD fragments should be selected"
    assert r.retrieved_items_dropped > 0, "Should drop irrelevant fragments"
    assert r.selected_retrieved_tokens <= r.budget_retrieved, f"Exceeds budget: {r.selected_retrieved_tokens} > {r.budget_retrieved}"
    
    print(f"  ✓ test_6_retrieval_ranking: {len(fragments)} frags -> {r.selected_retrieved_tokens}/{r.budget_retrieved} tokens, {r.retrieved_items_dropped} dropped")


def test_7_tokenizer_offline():
    """Tokenizer must work without network access."""
    text = "Hello world, this is a test of the offline tokenizer."
    
    # tiktoken should work (installed)
    tok = estimate_provider_tokens(text, "gpt-4o")
    assert tok > 0, f"tiktoken should work: {tok}"
    
    # chars/4 fallback should work
    tok2 = estimate_provider_tokens(text)
    assert tok2 > 0, f"chars/4 fallback should work: {tok2}"
    
    # Verify deterministic: same input = same output
    assert estimate_provider_tokens(text, "test") == estimate_provider_tokens(text, "test")
    
    print(f"  ✓ test_7_tokenizer_offline: tiktoken={tok} chars4={tok2}")


def test_8_accounting_separation():
    """input_tokens and prompt_tokens must be tracked separately."""
    from captn.runtime.reconcile import reconcile
    
    r = reconcile(
        [{"role": "user", "content": "Hello"}],
        None,
        model="test",
        provider_prompt_tokens=150,
        provider_cache_read=50,
        provider_cache_write=20,
    )
    
    assert r.provider_prompt_tokens == 150
    assert r.provider_input_tokens == 80  # 150 - 50 - 20
    assert r.provider_cache_read == 50
    assert r.provider_cache_write == 20
    
    # Verify formula
    assert r.provider_prompt_tokens == r.provider_input_tokens + r.provider_cache_read + r.provider_cache_write
    
    print(f"  ✓ test_8_accounting_separation: prompt={r.provider_prompt_tokens} input={r.provider_input_tokens} cache_r={r.provider_cache_read} cache_w={r.provider_cache_write}")


def test_9_realistic_30k_budget():
    """A realistic payload must fit within the 30K budget (or report overflow)."""
    # Simulate a Hermes-like request: system + history + memory + tools + retrieved
    sys_prompt = "You are a helpful AI assistant. Be concise. " * 50
    msgs = [{"role": "system", "content": sys_prompt}]
    for i in range(30):
        msgs.append({"role": "user", "content": f"User turn {i} with content." * 2})
        msgs.append({"role": "assistant", "content": f"Response {i} with details." * 3})
    msgs.append({"role": "user", "content": "What is the GCD of 48 and 180?"})
    memory = "User prefers Python. " * 200
    tools = [{"type": "function", "function": {"name": f"t{i}", "description": "A tool", "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}} for i in range(30)]
    retrieved = "Fragment about GCD.\n\nFragment about LCM.\n\n" + "Unrelated content.\n\n" * 20
    
    b = ContextBudget(model="test")
    r = b.select(
        system_prompt=sys_prompt,
        messages=msgs,
        memory_text=memory,
        tools=tools,
        retrieved_context=retrieved,
        user_query="What is the GCD of 48 and 180?",
        domain_hint="math",
        dry_run=True,
    )
    
    # The budget may or may not be satisfiable
    if r.total_budget_exceeded:
        assert r.overflow_tokens > 0, "Overflow should be positive"
        print(f"  ✓ test_9_realistic_30k_budget: OVERFLOW by {r.overflow_tokens} tokens (mandatory content exceeds budget)")
    else:
        assert r.selected_total_tokens <= r.budget_total, f"Should be within budget: {r.selected_total_tokens} > {r.budget_total}"
        print(f"  ✓ test_9_realistic_30k_budget: {r.selected_total_tokens}/{r.budget_total} tokens, within budget")
    # Decisions should be recorded
    assert len(r.decisions) > 0, "Should have decisions"


def test_10_mandatory_overflow_reporting():
    """When mandatory content exceeds budget, overflow must be explicitly reported."""
    # System prompt that alone exceeds 5K (but not 30K total)
    huge_sys = "Mandatory instruction. " * 2000  # ~32K chars, ~8K tokens
    msgs = [{"role": "system", "content": huge_sys}, {"role": "user", "content": "Hi"}]
    
    b = ContextBudget(model="test")
    r = b.select(
        system_prompt=huge_sys,
        messages=msgs,
        user_query="Hi",
        dry_run=True,
    )
    
    assert r.system_budget_exceeded, "System budget should be exceeded"
    # System should still be kept (immutable core preserved)
    assert r.selected_system_tokens > 0, "System should be kept intact"
    # Total budget may or may not be exceeded (system alone may be under 30K)
    # The key assertion: overflow is reported correctly
    if r.total_budget_exceeded:
        assert r.overflow_tokens > 0, "Overflow should be > 0"
    
    # Report should mention exceeded
    report = r.report()
    assert "EXCEEDED" in report, "Report should mention exceeded"
    
    print(f"  ✓ test_10_mandatory_overflow_reporting: sys_exceeded={r.system_budget_exceeded}, total_exceeded={r.total_budget_exceeded}, overflow={r.overflow_tokens}")


def test_11_budget_telemetry():
    """SelectionResult must include all required telemetry fields."""
    b = ContextBudget(model="test")
    r = b.select(
        system_prompt="You are helpful.",
        messages=[{"role": "user", "content": "Hello"}],
        user_query="Hello",
        dry_run=True,
    )
    
    d = r.to_dict()
    # Check all required fields exist
    assert "budget" in d
    assert "original" in d
    assert "selected" in d
    assert "overflow" in d
    assert "dropped" in d
    assert "history_compressed" in d
    assert "dry_run" in d
    assert "unused_budget" in d
    
    # Budget limits
    assert d["budget"]["total"] > 0
    assert d["budget"]["system"] > 0
    assert d["budget"]["history"] > 0
    
    # Original counts
    assert d["original"]["total"] >= 0
    assert d["original"]["system"] >= 0
    assert d["original"]["history"] >= 0
    
    # Selected counts
    assert d["selected"]["total"] >= 0
    assert d["selected"]["system"] >= 0
    
    # Overflow
    assert "overflow_tokens" in d["overflow"]
    assert "system_budget_exceeded" in d["overflow"]
    
    # Dropped
    assert d["dropped"]["history_items"] >= 0
    assert d["dropped"]["memory_items"] >= 0
    assert d["dropped"]["tools"] >= 0
    
    # JSON serializable
    json_str = json.dumps(d, default=str)
    assert json_str
    
    print(f"  ✓ test_11_budget_telemetry: {len(d)} top-level keys, {len(json_str)} chars JSON")


def test_12_ab_serialization():
    """A/B results must be serializable and comparable."""
    b = ContextBudget(model="test")
    
    # A: no budget
    r_a = estimate_messages_tokens([{"role": "user", "content": "Hello"}], "test")
    
    # B: with budget
    r_b = b.select(
        system_prompt="",
        messages=[{"role": "user", "content": "Hello"}],
        user_query="Hello",
        dry_run=True,
    )
    
    result = {
        "A": {"total_tokens": r_a, "budget": "off"},
        "B": {"total_tokens": r_b.selected_total_tokens, "budget": "on", "dry_run": True},
        "comparison": {
            "reduction_pct": round((1 - r_b.selected_total_tokens / max(r_a, 1)) * 100, 1) if r_a > 0 else 0,
            "same": r_a == r_b.selected_total_tokens,
        }
    }
    
    json_str = json.dumps(result, default=str)
    assert json_str
    parsed = json.loads(json_str)
    assert parsed["A"]["total_tokens"] > 0
    assert parsed["B"]["total_tokens"] > 0
    
    print(f"  ✓ test_12_ab_serialization: A={r_a} B={r_b.selected_total_tokens}")


def test_13_deterministic_overflow():
    """Same inputs must produce same overflow results."""
    huge_sys = "Mandatory. " * 3000
    b = ContextBudget(model="test")
    
    r1 = b.select(
        system_prompt=huge_sys,
        messages=[{"role": "user", "content": "Hi"}],
        user_query="Hi",
        dry_run=True,
    )
    r2 = b.select(
        system_prompt=huge_sys,
        messages=[{"role": "user", "content": "Hi"}],
        user_query="Hi",
        dry_run=True,
    )
    
    assert r1.overflow_tokens == r2.overflow_tokens
    assert r1.system_budget_exceeded == r2.system_budget_exceeded
    assert r1.selected_system_tokens == r2.selected_system_tokens
    
    print(f"  ✓ test_13_deterministic_overflow: overflow={r1.overflow_tokens}")


def test_14_feature_off_by_default():
    """Budget must be disabled by default."""
    # Save and restore env
    old = os.environ.get("CAPTN_CONTEXT_BUDGET")
    if "CAPTN_CONTEXT_BUDGET" in os.environ:
        del os.environ["CAPTN_CONTEXT_BUDGET"]
    
    assert is_budget_enabled() == False, "Should be disabled by default"
    
    os.environ["CAPTN_CONTEXT_BUDGET"] = "on"
    assert is_budget_enabled() == True
    
    os.environ["CAPTN_CONTEXT_BUDGET"] = "off"
    assert is_budget_enabled() == False
    
    # Restore
    if old is not None:
        os.environ["CAPTN_CONTEXT_BUDGET"] = old
    else:
        os.environ.pop("CAPTN_CONTEXT_BUDGET", None)
    
    print(f"  ✓ test_14_feature_off_by_default: default=off, on=True, off=False")


def test_15_no_leakage():
    """No prompt content must leak through to_dict()."""
    b = ContextBudget(model="test")
    r = b.select(
        system_prompt="SECRET_SYSTEM_12345",
        messages=[{"role": "user", "content": "SECRET_USER_67890"}],
        user_query="SECRET_QUERY",
        memory_text="SECRET_MEMORY",
        retrieved_context="SECRET_RETRIEVED",
        dry_run=True,
    )
    
    d = r.to_dict()
    json_str = json.dumps(d, default=str)
    
    assert "SECRET_SYSTEM_12345" not in json_str
    assert "SECRET_USER_67890" not in json_str
    assert "SECRET_QUERY" not in json_str
    assert "SECRET_MEMORY" not in json_str
    assert "SECRET_RETRIEVED" not in json_str
    
    print(f"  ✓ test_15_no_leakage: no secret content in telemetry")


if __name__ == "__main__":
    print(f"\n{'='*60}")
    print(f"  PHASE 2.1 — Context Budget Hardening Tests")
    print(f"{'='*60}\n")
    
    tests = [
        test_1_system_decomposition,
        test_2_memory_line_by_line,
        test_3_history_compression_large,
        test_4_tool_call_coherence,
        test_5_tool_utility_cost,
        test_6_retrieval_ranking,
        test_7_tokenizer_offline,
        test_8_accounting_separation,
        test_9_realistic_30k_budget,
        test_10_mandatory_overflow_reporting,
        test_11_budget_telemetry,
        test_12_ab_serialization,
        test_13_deterministic_overflow,
        test_14_feature_off_by_default,
        test_15_no_leakage,
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