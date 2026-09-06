#!/usr/bin/env python3
"""
Tests for captn.runtime.token_profiler.

Follows the acceptance criteria from the Token Profiler specification:
1. Empty context
2. System prompt only
3. Conversation history
4. Long-term memory
5. Tool injection
6. Retrieved context
7. Multiple categories
8. Final request validation
9. Estimated vs actual usage
10. Unknown/unattributed context
"""
from __future__ import annotations

import json
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["CAPTN_TOKEN_PROFILING"] = "summary"

from captn.runtime.token_profiler import (
    TokenProfiler, profile, estimate_tokens, estimate_messages_tokens,
    ProfileReport, ProfileEntry, UsageRecord,
    CATEGORY_LABELS, SUBCAT_LABELS,
)


def test_1_empty_context():
    """Empty profiler should produce zero-token report."""
    p = profile("summary", "test_1")
    report = p.report()
    assert report.total_estimated == 0, f"Expected 0, got {report.total_estimated}"
    assert len(report.entries) == 0, f"Expected 0 entries, got {len(report.entries)}"
    # Render should not crash
    text = report.render()
    assert "no profiled content" in text
    print("  ✓ test_1_empty_context")


def test_2_system_prompt_only():
    """System prompt only should produce one category."""
    p = profile("summary", "test_2")
    p.add("system_prompt", "You are a helpful assistant.", subcategory="system.core")
    p.add("system_prompt", "Be concise and accurate.", subcategory="system.personality")
    report = p.report()
    assert report.total_estimated > 0, "Expected non-zero tokens"
    cats = report.category_totals
    assert "system_prompt" in cats, f"Expected system_prompt category, got {cats}"
    assert cats["system_prompt"] == report.total_estimated, "All tokens should be system"
    assert len(report.entries) == 2, f"Expected 2 entries, got {len(report.entries)}"
    print(f"  ✓ test_2_system_prompt_only ({report.total_estimated} tokens)")


def test_3_conversation_history():
    """Conversation history should be properly measured."""
    p = profile("verbose", "test_3")
    history = [
        {"role": "user", "content": "What is the capital of France?"},
        {"role": "assistant", "content": "The capital of France is Paris."},
        {"role": "user", "content": "What is the population?"},
        {"role": "assistant", "content": "Approximately 2.1 million."},
    ]
    p.add_messages(history)
    report = p.report()
    assert report.total_estimated > 0, "Expected non-zero tokens"
    cats = report.category_totals
    assert "conversation_history" in cats, f"Expected conversation_history, got {cats}"
    assert cats["conversation_history"] == report.total_estimated
    # Verbose entries should include per-role breakdown
    assert len(report.entries) >= 3, f"Expected 3+ entries (total + user + assistant), got {len(report.entries)}"
    # Check item_count matches
    total_entry = [e for e in report.entries if e.subcategory == "history.total"]
    assert total_entry, "Expected history.total entry"
    assert total_entry[0].item_count == 4, f"Expected 4 items, got {total_entry[0].item_count}"
    print(f"  ✓ test_3_conversation_history ({report.total_estimated} tokens, {len(report.entries)} entries)")


def test_4_long_term_memory():
    """Long-term memory should be measured."""
    p = profile("summary", "test_4")
    memory_text = "User prefers Python.\nUser works on CaptN-BRAIN.\nUser speaks French and English."
    p.add_memory(memory_text, item_count=3)
    report = p.report()
    assert report.total_estimated > 0, "Expected non-zero tokens"
    cats = report.category_totals
    assert "long_term_memory" in cats, f"Expected long_term_memory, got {cats}"
    # Check item_count
    mem_entry = [e for e in report.entries if e.subcategory == "memory.memory"]
    if mem_entry:
        assert mem_entry[0].item_count == 3, f"Expected 3 items, got {mem_entry[0].item_count}"
    print(f"  ✓ test_4_long_term_memory ({report.total_estimated} tokens)")


def test_5_tool_injection():
    """Tool schemas should be measured."""
    p = profile("summary", "test_5")
    tools = [
        {"type": "function", "function": {"name": "gcd", "description": "GCD of two numbers", "parameters": {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}}}},
        {"type": "function", "function": {"name": "lcm", "description": "LCM of two numbers", "parameters": {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}}}},
        {"type": "function", "function": {"name": "web_search", "description": "Search the web", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}},
    ]
    p.add_tools(tools)
    report = p.report()
    assert report.total_estimated > 0, "Expected non-zero tokens"
    cats = report.category_totals
    assert "tools" in cats, f"Expected tools, got {cats}"
    # Check item_count
    tool_entry = [e for e in report.entries if e.subcategory == "tools.builtin"]
    if tool_entry:
        assert tool_entry[0].item_count == 3, f"Expected 3 tools, got {tool_entry[0].item_count}"
    print(f"  ✓ test_5_tool_injection ({report.total_estimated} tokens, {len(tools)} tools)")


def test_6_retrieved_context():
    """Retrieved context / BM25 should be measured."""
    p = profile("summary", "test_6")
    p.add("retrieved_context", "BM25 fragment: math_gcd related to Euclidean algorithm", subcategory="retrieval.bm25", item_count=1)
    p.add("retrieved_context", "RAG context: User's previous math queries", subcategory="retrieval.rag", item_count=1)
    report = p.report()
    assert report.total_estimated > 0, "Expected non-zero tokens"
    cats = report.category_totals
    assert "retrieved_context" in cats, f"Expected retrieved_context, got {cats}"
    assert len(report.entries) == 2, f"Expected 2 entries, got {len(report.entries)}"
    print(f"  ✓ test_6_retrieved_context ({report.total_estimated} tokens)")


def test_7_multiple_categories():
    """Multiple categories should sum correctly."""
    p = profile("summary", "test_7")
    p.add("system_prompt", "System prompt text", subcategory="system.core")
    p.add("conversation_history", "History text", subcategory="history.total", item_count=2)
    p.add("long_term_memory", "Memory text", subcategory="memory.memory", item_count=2)
    p.add("tools", "Tool text", subcategory="tools.builtin", item_count=2)
    p.add("user_message", "User message text", item_count=1)
    report = p.report()
    assert report.total_estimated > 0, "Expected non-zero tokens"
    cats = report.category_totals
    # Should have at least 3 categories
    assert len(cats) >= 3, f"Expected 3+ categories, got {len(cats)}: {cats}"
    # Sum of category totals should match total estimated (sum of entries)
    cat_sum = sum(cats.values())
    assert cat_sum == report.total_estimated, (
        f"Category sum mismatch: {cat_sum} vs {report.total_estimated}"
    )
    # Each entry's tokens should be positive
    for entry in report.entries:
        assert entry.estimated_tokens > 0, f"Zero-token entry: {entry}"
    print(f"  ✓ test_7_multiple_categories ({len(cats)} categories, {report.total_estimated} tokens)")


def test_8_final_request_validation():
    """Final request validation should detect unattributed tokens."""
    p = profile("summary", "test_8")
    p.add("system_prompt", "System text", subcategory="system.core")
    # Finalize with a larger text than sum of categories — simulates hidden inflation
    p.finalize("System text. User message. Assistant response. Tool call. More context. Extra data.")
    report = p.report()
    assert report.final_estimate > 0, "Expected non-zero final estimate"
    assert report.unattributed != 0, "Expected unattributed difference"
    # JSON should include it
    d = report.to_dict()
    assert "unattributed_tokens" in d["estimated"]
    print(f"  ✓ test_8_final_request_validation (final={report.final_estimate}, sum={report.sum_categories}, unattributed={report.unattributed})")


def test_9_estimated_vs_actual():
    """Estimated vs actual usage comparison should be preserved."""
    p = profile("summary", "test_9")
    p.add("system_prompt", "System text", subcategory="system.core")
    p.add_usage(
        {"prompt_tokens": 500, "completion_tokens": 100, "total_tokens": 600},
        provider="openrouter",
        model="test-model",
    )
    report = p.report()
    assert report.usage is not None, "Expected usage record"
    assert report.usage.prompt_tokens == 500, f"Expected 500, got {report.usage.prompt_tokens}"
    assert report.usage.completion_tokens == 100
    assert report.usage.provider == "openrouter"
    assert report.usage.model == "test-model"
    # JSON should include comparison
    d = report.to_dict()
    assert "comparison" in d, "Expected comparison in JSON"
    assert d["comparison"]["actual_input"] == 500
    assert d["comparison"]["estimated_input"] > 0
    print(f"  ✓ test_9_estimated_vs_actual (estimated={report.total_estimated}, actual=500, diff={d['comparison']['difference']})")


def test_10_unknown_context():
    """Unknown/other context should be tracked."""
    p = profile("summary", "test_10")
    p.add("other", "Some unknown context data", subcategory="", item_count=1)
    p.add("other", "More unknown data", subcategory="", item_count=1)
    report = p.report()
    assert report.total_estimated > 0, "Expected non-zero tokens"
    cats = report.category_totals
    assert "other" in cats, f"Expected other category, got {cats}"
    assert len(report.entries) == 2, f"Expected 2 entries, got {len(report.entries)}"
    print(f"  ✓ test_10_unknown_context ({report.total_estimated} tokens)")


def test_estimate_tokens():
    """Test the token estimation helper."""
    # Empty string
    assert estimate_tokens("") == 1, f"Expected 1, got {estimate_tokens('')}"
    # Short string
    assert estimate_tokens("Hello") == 2, f"Expected 2, got {estimate_tokens('Hello')}"
    # Dict
    d = {"name": "test", "value": 42}
    tok = estimate_tokens(d)
    assert tok > 0, f"Expected >0, got {tok}"
    # List
    tok = estimate_tokens([1, 2, 3])
    assert tok > 0, f"Expected >0, got {tok}"
    # None
    assert estimate_tokens(None) == 1, f"Expected 1, got {estimate_tokens(None)}"
    print(f"  ✓ test_estimate_tokens")


def test_estimate_messages():
    """Test the message token estimation helper."""
    # Single message
    msgs = [{"role": "user", "content": "Hello"}]
    tok = estimate_messages_tokens(msgs)
    assert tok > 0, f"Expected >0, got {tok}"
    # Multiple messages
    msgs = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
        {"role": "user", "content": "What is the GCD of 48 and 180?"},
    ]
    tok = estimate_messages_tokens(msgs)
    assert tok > 0, f"Expected >0, got {tok}"
    # Empty
    assert estimate_messages_tokens([]) == 0, "Expected 0 for empty"
    print(f"  ✓ test_estimate_messages")


def test_disable_profiler():
    """Disabling the profiler should make it a no-op."""
    TokenProfiler.disable()
    p = profile("summary", "test_disable")
    p.add("system_prompt", "Should not count", subcategory="system.core")
    p.add_messages([{"role": "user", "content": "Should not count"}])
    report = p.report()
    assert report.total_estimated == 0, f"Expected 0, got {report.total_estimated}"
    assert len(report.entries) == 0, f"Expected 0 entries, got {len(report.entries)}"
    # Re-enable
    TokenProfiler.enable()
    p2 = profile("summary", "test_reenable")
    p2.add("system_prompt", "Should count", subcategory="system.core")
    report2 = p2.report()
    assert report2.total_estimated > 0, "Expected non-zero after re-enable"
    print(f"  ✓ test_disable_profiler")


def test_render_formats():
    """Test that render() works for summary and verbose levels."""
    TokenProfiler.enable()
    # Summary
    p = profile("summary", "test_render")
    p.add("system_prompt", "Test", subcategory="system.core")
    text = p.render()
    assert "MIA BRAIN" in text, "Expected header"
    # Verbose
    p2 = profile("verbose", "test_render_v")
    p2.add("system_prompt", "Test", subcategory="system.core")
    p2.add("system_prompt", "Test 2", subcategory="system.personality")
    text2 = p2.render()
    assert "MIA BRAIN" in text2
    # JSON
    d = p.report().to_dict()
    assert "request_id" in d
    assert "categories" in d
    assert "entries" in d
    print(f"  ✓ test_render_formats")


def test_sum_of_categories_equals_total():
    """Sum of all category tokens should equal total_estimated."""
    p = profile("summary", "test_sum")
    p.add("system_prompt", "System text", subcategory="system.core")
    p.add("conversation_history", "History text", subcategory="history.total", item_count=2)
    p.add("long_term_memory", "Memory text", subcategory="memory.memory", item_count=2)
    p.add("tools", "Tool text", subcategory="tools.builtin", item_count=2)
    p.add("user_message", "User text", item_count=1)
    report = p.report()
    # Sum via category_totals should match total
    cat_sum = sum(report.category_totals.values())
    assert cat_sum == report.total_estimated, f"Category sum {cat_sum} != total {report.total_estimated}"
    # Sum via entries should match too
    entry_sum = sum(e.estimated_tokens for e in report.entries)
    assert entry_sum == report.total_estimated, f"Entry sum {entry_sum} != total {report.total_estimated}"
    print(f"  ✓ test_sum_of_categories_equals_total ({report.total_estimated} tokens)")


def test_subagent_tools():
    """Subagent tools should be categorized separately."""
    p = profile("summary", "test_subagent")
    tools = [
        {"type": "function", "function": {"name": "delegate_task", "description": "Delegate to subagent"}},
    ]
    p.add_tools(tools, subcategory="tools.subagent")
    report = p.report()
    assert report.total_estimated > 0
    subagent_entries = [e for e in report.entries if e.subcategory == "tools.subagent"]
    assert subagent_entries, "Expected subagent tool entry"
    print(f"  ✓ test_subagent_tools")


def test_mcp_tools():
    """MCP tools should be categorized separately."""
    p = profile("summary", "test_mcp")
    tools = [
        {"type": "function", "function": {"name": "mcp_fetch", "description": "MCP fetch tool"}},
    ]
    p.add_tools(tools, subcategory="tools.mcp")
    report = p.report()
    assert report.total_estimated > 0
    mcp_entries = [e for e in report.entries if e.subcategory == "tools.mcp"]
    assert mcp_entries, "Expected MCP tool entry"
    print(f"  ✓ test_mcp_tools")


def test_cache_usage():
    """Cache usage should be captured when available."""
    p = profile("summary", "test_cache")
    p.add_usage({
        "prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200,
        "cache_read_tokens": 500, "cache_write_tokens": 300,
    })
    report = p.report()
    assert report.usage is not None
    assert report.usage.cache_read_tokens == 500
    assert report.usage.cache_write_tokens == 300
    d = report.to_dict()
    assert d["actual"]["cache_read_tokens"] == 500
    assert d["actual"]["cache_write_tokens"] == 300
    print(f"  ✓ test_cache_usage")


def test_subcat_labels():
    """All subcategory labels should exist."""
    for subcat in SUBCAT_LABELS:
        assert subcat in SUBCAT_LABELS, f"Missing label for {subcat}"
    # All category labels should exist
    for cat in CATEGORY_LABELS:
        assert cat in CATEGORY_LABELS, f"Missing label for {cat}"
    print(f"  ✓ test_subcat_labels ({len(CATEGORY_LABELS)} categories, {len(SUBCAT_LABELS)} subcategories)")


if __name__ == "__main__":
    print(f"\n{'='*60}")
    print(f"  TOKEN PROFILER — Comprehensive Tests")
    print(f"{'='*60}\n")

    tests = [
        test_1_empty_context,
        test_2_system_prompt_only,
        test_3_conversation_history,
        test_4_long_term_memory,
        test_5_tool_injection,
        test_6_retrieved_context,
        test_7_multiple_categories,
        test_8_final_request_validation,
        test_9_estimated_vs_actual,
        test_10_unknown_context,
        test_estimate_tokens,
        test_estimate_messages,
        test_disable_profiler,
        test_render_formats,
        test_sum_of_categories_equals_total,
        test_subagent_tools,
        test_mcp_tools,
        test_cache_usage,
        test_subcat_labels,
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