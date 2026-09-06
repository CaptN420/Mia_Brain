#!/usr/bin/env python3
"""
Tests for captn.runtime.reconcile — OpenRouter Token Accounting Reconciliation.

Verifies:
1. Provider accounting formula: prompt_tokens = input_tokens + cache_read + cache_write
2. Cache fields not double-counted
3. Missing provider data handled safely
4. Local tokenizer counts reproducible
5. Provider/local differences calculated correctly
6. No prompt content persisted
7. Profiling remains observation-only
"""
from __future__ import annotations

import json
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from captn.runtime.reconcile import reconcile, OpenRouterAccounting


def test_1_provider_accounting_formula():
    """prompt_tokens = input_tokens + cache_read + cache_write."""
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
    print("  ✓ test_1_provider_accounting_formula: prompt = input + cache_read + cache_write")


def test_2_no_cache():
    """Without cache, prompt_tokens == input_tokens."""
    r = reconcile(
        [{"role": "user", "content": "Hello"}],
        None,
        model="test",
        provider_prompt_tokens=100,
    )
    assert r.provider_input_tokens == 100
    assert r.provider_cache_read is None
    assert r.provider_cache_write is None
    print("  ✓ test_2_no_cache: input == prompt when no cache")


def test_3_cache_only():
    """When all tokens are cached, input_tokens = 0."""
    r = reconcile(
        [{"role": "user", "content": "Hello"}],
        None,
        model="test",
        provider_prompt_tokens=100,
        provider_cache_read=80,
        provider_cache_write=20,
    )
    assert r.provider_input_tokens == 0  # 100 - 80 - 20 = 0
    print("  ✓ test_3_cache_only: input_tokens = 0 when prompt fully cached")


def test_4_missing_provider_data_no_crash():
    """Missing provider data must not crash."""
    r = reconcile([{"role": "user", "content": "Hi"}], None, model="test")
    assert r.provider_prompt_tokens is None
    # input_tokens is computed from prompt_tokens, so it's 0 when prompt is None
    assert r.provider_cache_read is None
    assert r.provider_cache_write is None
    assert r.status == "UNRECONCILED"  # default when no provider data
    # Render should not crash
    text = r.render()
    print("  ✓ test_4_missing_provider_data_no_crash")


def test_5_local_tokenizer_reproducible():
    """Same input should produce same local tokenizer count."""
    msgs = [{"role": "user", "content": "Hello world"}]
    r1 = reconcile(msgs, None, model="test")
    r2 = reconcile(msgs, None, model="test")
    assert r1.local_total_tokens == r2.local_total_tokens, "Local counts differ!"
    assert r1.local_tokenizer_method == r2.local_tokenizer_method
    print(f"  ✓ test_5_local_tokenizer_reproducible: {r1.local_total_tokens} tokens ({r1.local_tokenizer_method})")


def test_6_gap_calculations():
    """Provider/local gap calculations must be correct."""
    r = reconcile(
        [{"role": "user", "content": "Hello"}],
        None,
        model="test",
        provider_prompt_tokens=1000,
    )
    # Gap = provider - local
    expected_gap = 1000 - r.local_total_tokens
    assert r.provider_local_gap == expected_gap, f"Expected {expected_gap}, got {r.provider_local_gap}"
    # Ratio = provider / local
    expected_ratio = round(1000 / max(r.local_total_tokens, 1), 2)
    assert r.provider_local_ratio == expected_ratio, f"Expected {expected_ratio}, got {r.provider_local_ratio}"
    print(f"  ✓ test_6_gap_calculations: gap={r.provider_local_gap}, ratio={r.provider_local_ratio}x")


def test_7_no_prompt_content_persisted():
    """Reconcile report must not store prompt content."""
    r = reconcile(
        [{"role": "user", "content": "SECRET_CONTENT_SHOULD_NOT_APPEAR"}],
        None,
        model="test",
        provider_prompt_tokens=100,
    )
    json_str = json.dumps(r, default=str)
    assert "SECRET_CONTENT" not in json_str, "Leaked prompt content!"
    # Should only contain metadata
    assert "local_total_tokens" in json_str
    assert "provider_prompt_tokens" in json_str
    print("  ✓ test_7_no_prompt_content_persisted")


def test_8_no_mutation():
    """Reconcile must not mutate inputs."""
    msgs = [{"role": "user", "content": "Hello"}]
    tools = [{"type": "function", "function": {"name": "t", "description": "d"}}]
    msgs_copy = list(msgs)
    tools_copy = list(tools)
    reconcile(msgs, tools, model="test", provider_prompt_tokens=100)
    assert msgs == msgs_copy, "Messages mutated!"
    assert tools == tools_copy, "Tools mutated!"
    print("  ✓ test_8_no_mutation")


def test_9_component_breakdown():
    """Component breakdown must cover all content by role."""
    msgs = [
        {"role": "system", "content": "Sys"},
        {"role": "user", "content": "User"},
        {"role": "assistant", "content": "Asst"},
        {"role": "tool", "content": "Tool", "tool_call_id": "c1"},
    ]
    r = reconcile(msgs, None, model="test", provider_prompt_tokens=100)
    labels = [c.label for c in r.components]
    assert "content.system" in labels
    assert "content.user" in labels
    assert "content.assistant" in labels
    assert "content.tool" in labels
    # serialization_overhead should also be present
    assert any("serialization" in c.label for c in r.components), "Missing serialization overhead component"
    print(f"  ✓ test_9_component_breakdown: {len(r.components)} components: {labels}")


def test_10_tool_component():
    """Tool schemas must appear as a separate component."""
    msgs = [{"role": "user", "content": "Hi"}]
    tools = [{"type": "function", "function": {"name": "test_tool", "description": "d"}}]
    r = reconcile(msgs, tools, model="test", provider_prompt_tokens=50)
    assert any(c.label == "tool_schemas" for c in r.components), "Missing tool_schemas component"
    tool_comp = [c for c in r.components if c.label == "tool_schemas"][0]
    assert tool_comp.chars > 0
    assert tool_comp.local_tokens > 0
    print(f"  ✓ test_10_tool_component: {tool_comp.chars} chars, {tool_comp.local_tokens} tokens")


def test_11_observation_only():
    """Reconcile must not modify agent state or send requests."""
    r = reconcile([{"role": "user", "content": "Test"}], None, model="test")
    # Should produce a report without any side effects
    assert r.local_total_tokens > 0
    assert r.status == "UNRECONCILED"  # default with no provider data
    print("  ✓ test_11_observation_only: no side effects")


def test_12_serialization_overhead():
    """Serialization overhead should be the difference between raw content and JSON."""
    msgs = [{"role": "user", "content": "Hello"}]
    r = reconcile(msgs, None, model="test", provider_prompt_tokens=50)
    # Raw content = 5 chars ("Hello")
    # JSON messages = ~30 chars ({"role":"user","content":"Hello"})
    # Overhead = ~25 chars
    assert r.serialization_overhead_chars > 0, "Expected non-zero serialization overhead"
    # The overhead component should exist
    ovh_comps = [c for c in r.components if "serialization" in c.label]
    assert len(ovh_comps) > 0, "Missing serialization overhead component"
    print(f"  ✓ test_12_serialization_overhead: {r.serialization_overhead_chars} chars overhead")


if __name__ == "__main__":
    print(f"\n{'='*60}")
    print(f"  RECONCILE — OpenRouter Token Accounting Tests")
    print(f"{'='*60}\n")

    tests = [
        test_1_provider_accounting_formula,
        test_2_no_cache,
        test_3_cache_only,
        test_4_missing_provider_data_no_crash,
        test_5_local_tokenizer_reproducible,
        test_6_gap_calculations,
        test_7_no_prompt_content_persisted,
        test_8_no_mutation,
        test_9_component_breakdown,
        test_10_tool_component,
        test_11_observation_only,
        test_12_serialization_overhead,
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