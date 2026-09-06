#!/usr/bin/env python3
"""
Tests for captn.runtime.token_gap — Token Gap Investigation.

Verifies:
1. ProfileRecord does not mutate messages/tools
2. Duplicate detection works
3. Tool-size accounting works
4. Token-gap calculations are correct
5. Missing user does not crash
6. Profiling disabled by default
7. No prompt content persisted
"""
from __future__ import annotations

import json
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from captn.runtime.token_gap import (
    analyze_payload, _detect_duplicates, _analyze_tools,
    _count_tokens_real, extract_diagnostics,
    PayloadAnalysis, ToolSizeEntry,
)


def test_1_no_mutation():
    """Profiler must not mutate messages or tools."""
    msgs = [{"role": "user", "content": "Hello"}]
    tools = [{"type": "function", "function": {"name": "test_tool", "description": "A test"}}]
    msgs_copy = list(msgs)
    tools_copy = list(tools)

    analysis = analyze_payload(msgs, tools)
    assert msgs == msgs_copy, "Messages were mutated!"
    assert tools == tools_copy, "Tools were mutated!"
    print("  ✓ test_1_no_mutation: profiler does not mutate input")


def test_2_flat_json_serializable():
    """ProfileRecord must remain flat and JSON serializable."""
    diag = extract_diagnostics(
        [{"role": "user", "content": "Hello"}],
        [{"type": "function", "function": {"name": "t", "description": "d"}}],
        model="test-model",
        actual_input_tokens=1000,
    )
    # All values should be JSON-serializable
    json_str = json.dumps(diag, default=str)
    parsed = json.loads(json_str)
    assert isinstance(parsed, dict)
    assert "messages_chars" in parsed
    assert "tokenizer_total" in parsed
    assert "tokenizer_method" in parsed
    print("  ✓ test_2_flat_json_serializable: all fields JSON-serializable")


def test_3_duplicate_detection():
    """Duplicate detection must find exact duplicates."""
    msgs = [
        {"role": "user", "content": "Hello world"},
        {"role": "user", "content": "Hello world"},  # duplicate
        {"role": "assistant", "content": "Hi there"},
        {"role": "user", "content": "Hello world"},  # another duplicate
        {"role": "tool", "content": "Result A", "tool_call_id": "call_1"},
        {"role": "tool", "content": "Result A", "tool_call_id": "call_2"},  # duplicate
    ]
    result = _detect_duplicates(msgs)
    assert result["duplicate_count"] >= 2, f"Expected >=2 duplicates, got {result['duplicate_count']}"
    assert result["duplicate_chars"] > 0, "Expected non-zero duplicate chars"
    assert "user" in result["duplicate_by_type"], "Expected user duplicates"
    print(f"  ✓ test_3_duplicate_detection: {result['duplicate_count']} duplicates, {result['duplicate_chars']} chars")


def test_4_tool_size_accounting():
    """Tool-size accounting must report per-tool metrics."""
    tools = [
        {"type": "function", "function": {"name": "small_tool", "description": "S", "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}},
        {"type": "function", "function": {"name": "mcp_large", "description": "M" * 200, "parameters": {"type": "object", "properties": {"y": {"type": "string"}}}}},
        {"type": "function", "function": {"name": "delegate_task", "description": "Delegate", "parameters": {"type": "object", "properties": {"task": {"type": "string"}}}}},
    ]
    result = _analyze_tools(tools, model="test")
    assert result["tool_count"] == 3
    assert len(result["all_tools"]) == 3
    assert len(result["top_10"]) <= 3
    # Check MCP detection
    mcp_tools = [t for t in result["all_tools"] if t.is_mcp]
    assert len(mcp_tools) == 1, f"Expected 1 MCP tool, got {len(mcp_tools)}"
    assert mcp_tools[0].name == "mcp_large"
    # Check subagent detection
    subagent_tools = [t for t in result["all_tools"] if t.is_subagent]
    assert len(subagent_tools) == 1, f"Expected 1 subagent tool, got {len(subagent_tools)}"
    # Check by_source
    assert "tools.builtin" in result["by_source"]
    assert "tools.mcp" in result["by_source"]
    assert "tools.subagent" in result["by_source"]
    print(f"  ✓ test_4_tool_size_accounting: {result['tool_count']} tools, {result['total_chars']} chars")


def test_5_token_gap_calculations():
    """Token-gap calculations must be correct."""
    analysis = analyze_payload(
        [{"role": "user", "content": "Hello"}],
        [{"type": "function", "function": {"name": "t", "description": "d"}}],
        model="test-model",
        actual_input_tokens=1000,
    )
    assert analysis.actual_vs_estimated_ratio is not None
    assert analysis.actual_minus_estimated is not None
    # actual_minus_estimated = actual - heuristic
    expected_gap = 1000 - analysis.heuristic_total
    assert analysis.actual_minus_estimated == expected_gap, f"Gap mismatch: {analysis.actual_minus_estimated} vs {expected_gap}"
    # ratio = actual / heuristic
    expected_ratio = round(1000 / max(analysis.heuristic_total, 1), 2)
    assert analysis.actual_vs_estimated_ratio == expected_ratio, f"Ratio mismatch: {analysis.actual_vs_estimated_ratio} vs {expected_ratio}"
    print(f"  ✓ test_5_token_gap_calculations: gap={analysis.actual_minus_estimated}, ratio={analysis.actual_vs_estimated_ratio}x")


def test_6_missing_usage_does_not_crash():
    """Missing OpenRouter usage must not crash profiling."""
    analysis = analyze_payload(
        [{"role": "user", "content": "Hello"}],
        tools=None,
        model="test-model",
        actual_input_tokens=None,
    )
    assert analysis.actual_input_tokens is None
    assert analysis.actual_vs_estimated_ratio is None
    assert analysis.actual_minus_estimated is None
    assert analysis.heuristic_total > 0
    print(f"  ✓ test_6_missing_usage_does_not_crash: {analysis.heuristic_total} tokens, no actual")


def test_7_empty_payload():
    """Empty payload must not crash."""
    analysis = analyze_payload([], tools=None)
    assert analysis.heuristic_total == 0
    assert analysis.total_messages == 0
    assert analysis.tool_analysis.get("tool_count", 0) == 0
    print("  ✓ test_7_empty_payload: 0 tokens, 0 messages, 0 tools")


def test_8_tool_result_chars():
    """Tool result character counts must be correct."""
    msgs = [
        {"role": "tool", "content": "x" * 1000, "tool_call_id": "call_1"},
        {"role": "tool", "content": "x" * 2000, "tool_call_id": "call_2"},
    ]
    analysis = analyze_payload(msgs)
    assert analysis.tool_result_chars == 3000, f"Expected 3000, got {analysis.tool_result_chars}"
    assert analysis.tool_messages == 2, f"Expected 2 tool messages, got {analysis.tool_messages}"
    print(f"  ✓ test_8_tool_result_chars: {analysis.tool_result_chars} chars from {analysis.tool_messages} tool messages")


def test_9_diagnostics_fields():
    """Diagnostic fields must be consistent."""
    diag = extract_diagnostics(
        [{"role": "system", "content": "Sys"}, {"role": "user", "content": "Hello"}, {"role": "assistant", "content": "Hi"}, {"role": "user", "content": "Current"}],
        [{"type": "function", "function": {"name": "t", "description": "d"}}],
        model="test",
        actual_input_tokens=500,
    )
    assert diag["messages_chars"] > 0
    assert diag["tools_chars"] > 0
    assert diag["system_chars"] > 0
    assert diag["history_chars"] > 0  # user("Hello") + assistant("Hi")
    assert diag["current_user_chars"] > 0  # last user("Current")
    assert diag["actual_vs_estimated_ratio"] is not None
    assert diag["actual_minus_estimated"] is not None
    # Verify the gap is negative (actual > estimated) because the heuristic
    # underestimates tokens for DeepSeek models
    assert diag["actual_minus_estimated"] > 0, "Expected positive gap (actual > estimated)"
    assert diag["actual_vs_estimated_ratio"] > 1.0, "Expected ratio > 1.0 (actual > estimated)"
    print(f"  ✓ test_9_diagnostics_fields: {len(diag)} fields, gap={diag['actual_minus_estimated']}, ratio={diag['actual_vs_estimated_ratio']}x")


def test_10_no_prompt_content_persisted():
    """Diagnostics must not persist prompt content (only metadata)."""
    diag = extract_diagnostics(
        [{"role": "user", "content": "SECRET_CONTENT_SHOULD_NOT_BE_IN_DIAGNOSTICS"}],
        None,
    )
    json_str = json.dumps(diag, default=str)
    assert "SECRET_CONTENT" not in json_str, "Diagnostics leaked prompt content!"
    # Should only contain metadata
    assert "messages_chars" in json_str
    assert "tokenizer_method" in json_str
    print("  ✓ test_10_no_prompt_content_persisted: no secrets in diagnostics")


if __name__ == "__main__":
    print(f"\n{'='*60}")
    print(f"  TOKEN GAP INVESTIGATION — Tests")
    print(f"{'='*60}\n")

    tests = [
        test_1_no_mutation,
        test_2_flat_json_serializable,
        test_3_duplicate_detection,
        test_4_tool_size_accounting,
        test_5_token_gap_calculations,
        test_6_missing_usage_does_not_crash,
        test_7_empty_payload,
        test_8_tool_result_chars,
        test_9_diagnostics_fields,
        test_10_no_prompt_content_persisted,
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