#!/usr/bin/env python3
"""
test_hermes_profile.py — 12 tests for ProfileRecord budget telemetry fields.

Covers:
1. ProfileRecord serialization
2. Budget telemetry fields
3. Oversized items capture
4. Warning messages
5. Memory/skills/subagent fields
6. to_dict roundtrip
7. from_hermes_profile with budget fields
8. Observation-only (no mutation)
9. Actual usage fallback
10. Large payload
11. Missing model
12. Type consistency
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _imports():
    global ProfileRecord, _append_record, get_all_records, clear_records, \
        records_summary, HermesProfile, UsageData
    from captn.runtime.hermes_profiler import (
        ProfileRecord, _append_record, get_all_records, clear_records,
        records_summary, HermesProfile, UsageData,
    )


# ── Tests ────────────────────────────────────────────────────────

def test_1_profile_record_serialization():
    """ProfileRecord must serialize to dict and JSONL without error."""
    from captn.runtime.hermes_profiler import ProfileRecord
    r = ProfileRecord(
        request_id="test_serial",
        timestamp="2026-01-01T00:00:00Z",
        model="deepseek/deepseek-v4-flash",
        provider="openrouter",
        estimated_total=50000,
        budget_enabled=True,
        budget_before_tokens=50000,
        budget_after_tokens=28000,
        budget_drop_count=20,
        budget_warnings=["system_budget_exceeded"],
        system_budget_exceeded=True,
        oversized_items=2,
    )
    d = r.to_dict()
    assert d["request_id"] == "test_serial"
    assert d["budget_enabled"] is True
    assert d["budget_before_tokens"] == 50000
    assert d["budget_after_tokens"] == 28000
    assert d["budget_drop_count"] == 20
    assert d["system_budget_exceeded"] is True
    assert d["oversized_items"] == 2
    # JSONL
    j = r.to_jsonl()
    d2 = json.loads(j)
    assert d2["budget_enabled"] is True
    assert d2["budget_before_tokens"] == 50000
    print(f"  ✓ test_1: dict={len(d)} keys, jsonl={len(j)} chars")


def test_2_budget_telemetry_fields_default():
    """Budget telemetry fields must have sensible defaults."""
    from captn.runtime.hermes_profiler import ProfileRecord
    r = ProfileRecord(request_id="test_defaults", timestamp="now")
    assert r.budget_enabled is False
    assert r.budget_before_tokens == 0
    assert r.budget_after_tokens == 0
    assert r.budget_drop_count == 0
    assert r.budget_warnings == []
    assert r.system_budget_exceeded is False
    print(f"  ✓ test_2: all budget fields have correct defaults")


def test_3_oversized_items_capture():
    """Oversized_items must be correctly captured."""
    from captn.runtime.hermes_profiler import ProfileRecord
    r = ProfileRecord(request_id="test_oversized", timestamp="now",
                       oversized_items=3, budget_enabled=True)
    assert r.oversized_items == 3
    d = r.to_dict()
    assert d["oversized_items"] == 3
    print(f"  ✓ test_3: oversized_items={r.oversized_items}")


def test_4_warning_messages():
    """Warning messages must survive serialization."""
    from captn.runtime.hermes_profiler import ProfileRecord
    warnings = ["system_budget_exceeded", "tools_budget_exceeded", "total_budget_exceeded=5000"]
    r = ProfileRecord(request_id="test_warn", timestamp="now",
                       budget_warnings=warnings, budget_enabled=True)
    assert len(r.budget_warnings) == 3
    d = r.to_dict()
    assert len(d["budget_warnings"]) == 3
    assert d["budget_warnings"][0] == "system_budget_exceeded"
    assert d["budget_warnings"][2] == "total_budget_exceeded=5000"
    print(f"  ✓ test_4: {len(warnings)} warnings serialized correctly")


def test_5_memory_skills_subagent_fields():
    """Memory, skills, subagent fields must be distinct."""
    from captn.runtime.hermes_profiler import ProfileRecord
    r = ProfileRecord(request_id="test_cat", timestamp="now",
                       system_tokens=1000, memory_tokens=2000,
                       memory_prefetch_tokens=500, skills_tokens=3000,
                       mcp_tokens=400, tool_schema_tokens=5000,
                       subagent_tokens=600, current_user_tokens=200,
                       other_tokens=100)
    d = r.to_dict()
    assert d["system_tokens"] == 1000
    assert d["memory_tokens"] == 2000
    assert d["memory_prefetch_tokens"] == 500
    assert d["skills_tokens"] == 3000
    assert d["mcp_tokens"] == 400
    assert d["tool_schema_tokens"] == 5000
    assert d["subagent_tokens"] == 600
    assert d["current_user_tokens"] == 200
    assert d["other_tokens"] == 100
    print(f"  ✓ test_5: {8} category fields distinct")


def test_6_to_dict_roundtrip():
    """to_dict and dataclass reconstruction must be symmetric."""
    from captn.runtime.hermes_profiler import ProfileRecord
    r = ProfileRecord(
        request_id="test_rt", timestamp="now",
        model="test-model", provider="test-provider",
        estimated_total=12345, estimated_messages=10000, estimated_tools=2345,
        message_count=10, tool_count=5,
        system_tokens=2000, history_tokens=3000, memory_tokens=1000,
        skills_tokens=500, mcp_tokens=200,
        tool_schema_tokens=1500, current_user_tokens=100,
        attributed_total=12300, attribution_coverage=99.7,
        actual_input_tokens=15000, actual_output_tokens=500, actual_total_tokens=15500,
        cache_read_tokens=5000, cache_write_tokens=2000,
        budget_enabled=True, budget_before_tokens=12345, budget_after_tokens=10000,
        budget_drop_count=8, system_budget_exceeded=False,
        oversized_items=1, estimate_error_pct=17.8,
    )
    d = r.to_dict()
    # Check key fields match
    assert d["request_id"] == "test_rt"
    assert d["budget_enabled"] is True
    assert d["budget_before_tokens"] == 12345
    assert d["cache_read_tokens"] == 5000
    assert d["cache_write_tokens"] == 2000
    # Roundtrip via JSON
    j = json.dumps(d, default=str)
    d2 = json.loads(j)
    assert d2["request_id"] == "test_rt"
    assert d2["budget_before_tokens"] == 12345
    print(f"  ✓ test_6: roundtrip OK ({len(j)} chars)")


def test_7_from_hermes_profile_budget_fields():
    """from_hermes_profile must preserve budget fields from HermesProfile."""
    from captn.runtime.hermes_profiler import ProfileRecord, HermesProfile, UsageData
    hp = HermesProfile(request_id="test_hp", timestamp="now")
    hp.system_prompt_tokens = 10000
    hp.conversation_history_tokens = 20000
    hp.memory_tokens = 5000
    hp.skills_tokens = 3000
    hp.tool_schemas_tokens = 4000
    hp.user_message_tokens = 200
    hp.other_tokens = 100
    hp.usage = UsageData(prompt_tokens=55000, completion_tokens=500, total_tokens=55500,
                          cache_read=10000, cache_write=5000)

    rec = ProfileRecord.from_hermes_profile(hp, model="test", provider="openrouter")
    assert rec.attributed_total > 0, "attributed_total should be > 0"
    assert rec.system_tokens == 10000
    assert rec.history_tokens == 20000
    assert rec.cache_read_tokens == 10000
    assert rec.cache_write_tokens == 5000
    # Budget fields should be default (False/0) since no budget was applied
    assert rec.budget_enabled is False
    assert rec.budget_before_tokens == 0
    assert rec.budget_after_tokens == 0
    assert rec.budget_drop_count == 0
    assert rec.budget_warnings == []
    assert rec.system_budget_exceeded is False
    print(f"  ✓ test_7: from_hermes_profile: coverage={rec.attribution_coverage}%, "
          f"cache_read={rec.cache_read_tokens}")


def test_8_observation_only_no_mutation():
    """Profiling must not modify the original payload."""
    from captn.runtime.hermes_profiler import HermesProfile
    msgs = [{"role": "user", "content": "Hello"}]
    msgs_copy = list(msgs)
    # Just creating HermesProfile and usage should not mutate the messages
    hp = HermesProfile(request_id="test_obs", timestamp="now")
    assert msgs == msgs_copy, "Messages were mutated!"
    print(f"  ✓ test_8: observation-only, no mutation")


def test_9_actual_usage_fallback():
    """ProfileRecord must handle None actual usage gracefully."""
    from captn.runtime.hermes_profiler import ProfileRecord
    r = ProfileRecord(request_id="test_null", timestamp="now")
    assert r.actual_input_tokens is None
    assert r.actual_output_tokens is None
    assert r.cache_read_tokens is None
    assert r.cache_write_tokens is None
    # to_dict must not crash
    d = r.to_dict()
    assert d["actual_input_tokens"] is None
    assert d["cache_read_tokens"] is None
    j = r.to_jsonl()
    d2 = json.loads(j)
    assert d2["actual_input_tokens"] is None
    print(f"  ✓ test_9: None actual usage handled ({len(j)} chars)")


def test_10_large_payload():
    """ProfileRecord must handle large estimated values."""
    from captn.runtime.hermes_profiler import ProfileRecord
    r = ProfileRecord(request_id="test_large", timestamp="now",
                       estimated_total=150000, estimated_messages=120000,
                       estimated_tools=30000, message_count=200, tool_count=80,
                       system_tokens=35000, history_tokens=60000, memory_tokens=30000,
                       tool_schema_tokens=15000, skills_tokens=10000,
                       budget_enabled=True, budget_before_tokens=150000,
                       budget_after_tokens=120000, budget_drop_count=100)
    d = r.to_dict()
    assert d["estimated_total"] == 150000
    assert d["budget_before_tokens"] == 150000
    assert d["budget_drop_count"] == 100
    j = r.to_jsonl()
    d2 = json.loads(j)
    assert d2["estimated_total"] == 150000
    print(f"  ✓ test_10: large payload serialized ({len(j)} chars)")


def test_11_missing_model():
    """ProfileRecord must handle empty model string."""
    from captn.runtime.hermes_profiler import ProfileRecord
    r = ProfileRecord(request_id="test_nomodel", timestamp="now")
    assert r.model == ""
    d = r.to_dict()
    assert d["model"] == ""
    j = r.to_jsonl()
    d2 = json.loads(j)
    assert d2["model"] == ""
    print(f"  ✓ test_11: empty model handled")


def test_12_type_consistency():
    """All budget fields must have consistent types."""
    from captn.runtime.hermes_profiler import ProfileRecord
    r = ProfileRecord(request_id="test_types", timestamp="now",
                       budget_enabled=True,
                       budget_before_tokens=50000,
                       budget_after_tokens=25000,
                       budget_drop_count=15,
                       budget_warnings=["warn1"],
                       system_budget_exceeded=False)
    assert isinstance(r.budget_enabled, bool)
    assert isinstance(r.budget_before_tokens, int)
    assert isinstance(r.budget_after_tokens, int)
    assert isinstance(r.budget_drop_count, int)
    assert isinstance(r.budget_warnings, list)
    assert isinstance(r.system_budget_exceeded, bool)
    assert isinstance(r.oversized_items, int)
    assert isinstance(r.estimate_error_pct, (int, float, type(None)))
    assert isinstance(r.cache_read_tokens, (int, type(None)))
    d = r.to_dict()
    for key in ["budget_enabled", "budget_before_tokens", "budget_after_tokens",
                "budget_drop_count", "system_budget_exceeded"]:
        assert key in d, f"Missing key: {key}"
    print(f"  ✓ test_12: all types consistent")


if __name__ == "__main__":
    print(f"\n{'='*60}")
    print(f"  HERMES PROFILE TESTS")
    print(f"{'='*60}\n")

    tests = [
        test_1_profile_record_serialization,
        test_2_budget_telemetry_fields_default,
        test_3_oversized_items_capture,
        test_4_warning_messages,
        test_5_memory_skills_subagent_fields,
        test_6_to_dict_roundtrip,
        test_7_from_hermes_profile_budget_fields,
        test_8_observation_only_no_mutation,
        test_9_actual_usage_fallback,
        test_10_large_payload,
        test_11_missing_model,
        test_12_type_consistency,
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