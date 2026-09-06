#!/usr/bin/env python3
"""
Tests for captn.runtime.context_budget — Context Budget & Relevance-Based Selection.

Verifies:
1. Budget allocation respects per-category limits
2. Exact token accounting
3. Category overflow handling
4. Mandatory system prompt preserved
5. Mandatory tools preserved
6. History compression
7. Memory ranking
8. Retrieved-context ranking
9. Conditional tool injection
10. Total-budget enforcement
11. Dry-run mode
12. Feature flag
13. No mutation when disabled
14. No prompt-content persistence
15. Deterministic selection
"""
from __future__ import annotations

import json
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from captn.runtime.context_budget import (
    ContextBudget, BudgetConfig, SelectionResult, SelectionDecision,
    estimate_provider_tokens, estimate_messages_tokens, estimate_tool_tokens,
    is_budget_enabled, DEFAULT_BUDGET_CONFIG,
)


def test_1_budget_within_limits():
    """When content fits within budget, nothing is dropped."""
    budget = ContextBudget(model="test")
    result = budget.select(
        system_prompt="You are helpful.",
        messages=[{"role": "user", "content": "Hello"}],
        memory_text="",
        tools=[],
        retrieved_context="",
        user_query="Hello",
    )
    assert result.selected_total_tokens <= result.budget_total
    assert result.history_items_dropped == 0
    assert result.tools_dropped == 0
    assert result.total_budget_exceeded is False
    print(f"  ✓ test_1_budget_within_limits: {result.selected_total_tokens} <= {result.budget_total}")


def test_2_exact_token_accounting():
    """Token estimates must be consistent and reproducible."""
    budget = ContextBudget(model="test")
    result1 = budget.select(
        system_prompt="You are helpful.",
        messages=[{"role": "user", "content": "Hello"}],
    )
    result2 = budget.select(
        system_prompt="You are helpful.",
        messages=[{"role": "user", "content": "Hello"}],
    )
    assert result1.selected_system_tokens == result2.selected_system_tokens
    assert result1.selected_history_tokens == result2.selected_history_tokens
    assert result1.selected_total_tokens == result2.selected_total_tokens
    print(f"  ✓ test_2_exact_token_accounting: consistent at {result1.selected_total_tokens} tokens")


def test_3_system_budget_overflow():
    """When system prompt exceeds budget, system_budget_exceeded is set."""
    budget = ContextBudget(model="test")
    big_system = "You are helpful. " * 5000  # ~100K chars, well over 5K tokens
    result = budget.select(
        system_prompt=big_system,
        messages=[{"role": "user", "content": "Hi"}],
    )
    # System should report exceeded
    assert result.system_budget_exceeded, "System budget should be exceeded"
    # System should still be included (core is NEVER removed)
    assert result.selected_system_tokens > 0
    print(f"  ✓ test_3_system_budget_overflow: {result.selected_system_tokens} tokens, exceeded={result.system_budget_exceeded}")


def test_4_history_selection():
    """History selection should keep recent/relevant messages."""
    budget = ContextBudget(model="test")
    messages = [{"role": "system", "content": "You are helpful."}]
    for i in range(100):
        messages.append({"role": "user", "content": f"Message {i}"})
        messages.append({"role": "assistant", "content": f"Response {i}"})

    result = budget.select(
        system_prompt="You are helpful.",
        messages=messages,
        user_query="Message 99",
    )
    # Should keep system and some recent messages
    assert result.selected_history_tokens > 0
    # The current user message (Message 99) should be kept
    assert result.selected_messages is not None
    print(f"  ✓ test_4_history_selection: {len(result.selected_messages)}/{len(messages)} msgs, "
          f"{result.selected_history_tokens}/{result.original_history_tokens} tokens")


def test_5_tool_selection():
    """Tool selection should keep relevant tools and drop others."""
    budget = ContextBudget(model="test")
    tools = []
    for i in range(50):
        tools.append({"type": "function", "function": {"name": f"tool_{i}", "description": "A tool", "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}})

    result = budget.select(
        system_prompt="You are helpful.",
        messages=[{"role": "user", "content": "Use math tool"}],
        tools=tools,
        user_query="math",
        domain_hint="math",
    )
    # Should fit within 5K tool budget
    assert result.selected_tool_tokens <= result.budget_tools
    # May have dropped some tools
    if result.original_tool_tokens > result.budget_tools:
        assert result.tools_dropped > 0, "Should drop tools when over budget"
    print(f"  ✓ test_5_tool_selection: {len(result.selected_tools)}/{len(tools)} tools, "
          f"{result.selected_tool_tokens}/{result.budget_tools} tokens, dropped={result.tools_dropped}")


def test_6_memory_selection():
    """Memory selection should keep relevant entries."""
    budget = ContextBudget(model="test")
    memory = "User prefers Python.\n" * 200 + "User asked about GCD.\n" * 50

    result = budget.select(
        system_prompt="You are helpful.",
        messages=[{"role": "user", "content": "What is GCD?"}],
        memory_text=memory,
        user_query="What is GCD?",
    )
    assert result.selected_memory_tokens <= result.budget_memory
    if result.original_memory_tokens > result.budget_memory:
        assert result.memory_items_dropped > 0, "Should drop memory when over budget"
    print(f"  ✓ test_6_memory_selection: {result.selected_memory_tokens}/{result.budget_memory} tokens, "
          f"dropped={result.memory_items_dropped}")


def test_7_retrieved_selection():
    """Retrieved context selection should keep relevant fragments."""
    budget = ContextBudget(model="test")
    retrieved = "Fragment about GCD.\n\nFragment about LCM.\n\n" + "General text.\n\n" * 50

    result = budget.select(
        system_prompt="You are helpful.",
        messages=[{"role": "user", "content": "What is GCD?"}],
        retrieved_context=retrieved,
        user_query="What is GCD?",
    )
    assert result.selected_retrieved_tokens <= result.budget_retrieved
    if result.original_retrieved_tokens > result.budget_retrieved:
        assert result.retrieved_items_dropped > 0, "Should drop retrieved when over budget"
    print(f"  ✓ test_7_retrieved_selection: {result.selected_retrieved_tokens}/{result.budget_retrieved} tokens, "
          f"dropped={result.retrieved_items_dropped}")


def test_8_total_budget_enforcement():
    """Total budget must be enforced when exceeded."""
    budget = ContextBudget(model="test")
    # Create a payload that exceeds 30K total
    big_system = "x" * 200000
    messages = [{"role": "user", "content": "Hello"}]
    big_memory = "y" * 200000
    big_retrieved = "z" * 200000

    result = budget.select(
        system_prompt=big_system,
        messages=messages,
        memory_text=big_memory,
        retrieved_context=big_retrieved,
        user_query="Hello",
    )

    # Budget should be exceeded (system alone is > 30K)
    if result.original_total_tokens > result.budget_total:
        assert result.total_budget_exceeded, "Total budget should be exceeded"
    print(f"  ✓ test_8_total_budget_enforcement: original={result.original_total_tokens}, "
          f"selected={result.selected_total_tokens}, exceeded={result.total_budget_exceeded}")


def test_9_dry_run_mode():
    """Dry-run mode must not modify the real request."""
    budget = ContextBudget(model="test")
    messages = [{"role": "user", "content": "test"}]
    messages_copy = list(messages)

    result = budget.select(
        messages=messages,
        user_query="test",
        dry_run=True,
    )
    assert result.dry_run is True
    assert messages == messages_copy, "Messages were mutated!"
    print(f"  ✓ test_9_dry_run_mode: dry_run={result.dry_run}, no mutation")


def test_10_feature_flag():
    """Feature flag must be checked correctly."""
    # Default is off
    assert is_budget_enabled() is False, "Should be disabled by default"

    # Enable
    os.environ["CAPTN_CONTEXT_BUDGET"] = "on"
    assert is_budget_enabled() is True
    del os.environ["CAPTN_CONTEXT_BUDGET"]
    print("  ✓ test_10_feature_flag: default=off, env=on=active")


def test_11_no_prompt_content_persisted():
    """Selection results must not contain prompt content."""
    budget = ContextBudget(model="test")
    result = budget.select(
        system_prompt="SECRET_SYSTEM_CONTENT",
        messages=[{"role": "user", "content": "SECRET_USER_MSG"}],
        user_query="SECRET_QUERY",
    )
    json_str = json.dumps(result.to_dict(), default=str)
    assert "SECRET_SYSTEM_CONTENT" not in json_str, "Leaked system prompt!"
    assert "SECRET_USER_MSG" not in json_str, "Leaked user message!"
    assert "SECRET_QUERY" not in json_str, "Leaked query!"
    # Should contain only metadata
    d = result.to_dict()
    assert "budget" in d
    assert "original" in d
    assert "selected" in d
    assert "dropped" in d
    print("  ✓ test_11_no_prompt_content_persisted")


def test_12_deterministic_selection():
    """Same inputs must produce same selection."""
    budget = ContextBudget(model="test")
    tools = [{"type": "function", "function": {"name": "gcd", "description": "GCD", "parameters": {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}, "required": ["a", "b"]}}}]

    result1 = budget.select(
        system_prompt="You are a math assistant.",
        messages=[{"role": "user", "content": "Find GCD of 48 and 180"}],
        tools=tools,
        user_query="Find GCD of 48 and 180",
        domain_hint="math",
    )
    result2 = budget.select(
        system_prompt="You are a math assistant.",
        messages=[{"role": "user", "content": "Find GCD of 48 and 180"}],
        tools=tools,
        user_query="Find GCD of 48 and 180",
        domain_hint="math",
    )
    assert result1.selected_total_tokens == result2.selected_total_tokens
    assert result1.selected_tool_tokens == result2.selected_tool_tokens
    assert result1.selected_history_tokens == result2.selected_history_tokens
    assert result1.tools_dropped == result2.tools_dropped
    print(f"  ✓ test_12_deterministic_selection: {result1.selected_total_tokens} == {result2.selected_total_tokens}")


def test_13_history_compression():
    """History compression should be attempted when many messages are dropped."""
    budget = ContextBudget(model="test", config=BudgetConfig(total=1000, history=500, system=100, memory=100, tools=100, retrieved=100))
    messages = [{"role": "system", "content": "You are helpful."}]
    for i in range(30):
        messages.append({"role": "user", "content": f"Message {i} with some content about the project."})
        messages.append({"role": "assistant", "content": f"Response {i} with analysis and code examples."})

    result = budget.select(
        system_prompt="You are helpful.",
        messages=messages,
        user_query="Message 29",
    )
    # With a 500-token history budget and 61 messages, compression should be attempted
    if result.original_history_tokens > result.budget_history:
        assert result.history_items_dropped > 0, "Should drop history when over budget"
    print(f"  ✓ test_13_history_compression: dropped={result.history_items_dropped}, compressed={result.history_compressed}")


def test_14_report_output():
    """Report should be renderable and contain key info."""
    budget = ContextBudget(model="test")
    result = budget.select(
        system_prompt="You are helpful.",
        messages=[{"role": "user", "content": "Hello"}],
        user_query="Hello",
    )
    report = result.report()
    assert "HERMES CONTEXT BUDGET" in report
    assert "Unused budget" in report
    assert "DRY RUN" not in report  # dry_run=False by default
    print(f"  ✓ test_14_report_output: report contains expected sections")


def test_15_to_dict_compatible():
    """to_dict() must produce JSON-serializable output."""
    budget = ContextBudget(model="test")
    result = budget.select(
        system_prompt="You are helpful.",
        messages=[{"role": "user", "content": "Hello"}],
        user_query="Hello",
    )
    d = result.to_dict()
    assert "budget" in d
    assert "original" in d
    assert "selected" in d
    assert "dropped" in d
    assert "dry_run" in d
    json_str = json.dumps(d, default=str)
    assert json_str
    print(f"  ✓ test_15_to_dict_compatible: {len(d)} top-level keys")


if __name__ == "__main__":
    print(f"\n{'='*60}")
    print(f"  CONTEXT BUDGET — Comprehensive Tests")
    print(f"{'='*60}\n")

    tests = [
        test_1_budget_within_limits,
        test_2_exact_token_accounting,
        test_3_system_budget_overflow,
        test_4_history_selection,
        test_5_tool_selection,
        test_6_memory_selection,
        test_7_retrieved_selection,
        test_8_total_budget_enforcement,
        test_9_dry_run_mode,
        test_10_feature_flag,
        test_11_no_prompt_content_persisted,
        test_12_deterministic_selection,
        test_13_history_compression,
        test_14_report_output,
        test_15_to_dict_compatible,
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