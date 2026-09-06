#!/usr/bin/env python3
"""
test_budget_telemetry.py — 11 tests for Context Budget telemetry.

Covers:
1. Budget telemetry field creation
2. _add_budget_telemetry populates fields
3. to_dict() includes budget fields
4. fields() includes budget columns
5. budget_summary with records
6. budget_summary empty when no budget records
7. records_summary includes budget aggregates
8. JSONL serialization of budget fields
9. Clear records works
10. Warnings accumulated correctly
11. Rollback flag works via feature flag
"""
from __future__ import annotations

import json
import os
import sys
import threading
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _imports():
    global ProfileRecord, _add_budget_telemetry, budget_summary, \
        get_all_records, clear_records, records_summary, records_csv, \
        _append_record
    from captn.runtime.hermes_profiler import (
        ProfileRecord, _add_budget_telemetry, budget_summary,
        get_all_records, clear_records, records_summary, records_csv,
        _append_record,
    )
    from captn.runtime.context_budget import SelectionResult, BudgetConfig, ContextBudget


# ── Helpers ──────────────────────────────────────────────────────

def make_selection_result(
    before: int = 50000,
    after: int = 28000,
    hist_drop: int = 5,
    mem_drop: int = 10,
    tools_drop: int = 2,
    ret_drop: int = 3,
    sys_exceeded: bool = False,
    tools_exceeded: bool = False,
    total_exceeded: bool = False,
    overflow: int = 0,
):
    """Create a SelectionResult with desired telemetry values."""
    from captn.runtime.context_budget import SelectionResult, BudgetConfig
    config = BudgetConfig()
    return SelectionResult(
        original_system_tokens=10000,
        original_history_tokens=20000,
        original_memory_tokens=10000,
        original_tool_tokens=5000,
        original_retrieved_tokens=5000,
        original_total_tokens=before,
        selected_system_tokens=5000 if not sys_exceeded else 10000,
        selected_history_tokens=10000,
        selected_memory_tokens=5000,
        selected_tool_tokens=3000 if not tools_exceeded else 5000,
        selected_retrieved_tokens=3000,
        selected_total_tokens=after,
        budget_total=config.total,
        budget_system=config.system,
        budget_history=config.history,
        budget_memory=config.memory,
        budget_tools=config.tools,
        budget_retrieved=config.retrieved,
        system_budget_exceeded=sys_exceeded,
        tools_budget_exceeded=tools_exceeded,
        total_budget_exceeded=total_exceeded,
        history_items_dropped=hist_drop,
        memory_items_dropped=mem_drop,
        retrieved_items_dropped=ret_drop,
        tools_dropped=tools_drop,
        decisions=[],
        selected_messages=[],
        selected_tools=[],
        selected_memory_text="",
        selected_retrieved_text="",
        selected_system_text="",
        history_compressed=False,
        history_compressed_savings=0,
        dry_run=True,
    )


# ── Tests ────────────────────────────────────────────────────────

def test_1_budget_fields_exist():
    """ProfileRecord must have budget telemetry fields."""
    from captn.runtime.hermes_profiler import ProfileRecord
    r = ProfileRecord(request_id="test_1", timestamp="now", model="test")
    assert hasattr(r, "budget_enabled")
    assert hasattr(r, "budget_before_tokens")
    assert hasattr(r, "budget_after_tokens")
    assert hasattr(r, "budget_drop_count")
    assert hasattr(r, "budget_warnings")
    assert hasattr(r, "system_budget_exceeded")
    # Defaults
    assert r.budget_enabled is False
    assert r.budget_before_tokens == 0
    assert r.budget_after_tokens == 0
    assert r.budget_drop_count == 0
    assert r.budget_warnings == []
    assert r.system_budget_exceeded is False
    print(f"  ✓ test_1: all 6 budget fields exist with correct defaults")


def test_2_add_budget_telemetry_populates_fields():
    """_add_budget_telemetry must populate all budget fields correctly."""
    from captn.runtime.hermes_profiler import ProfileRecord, _add_budget_telemetry
    r = ProfileRecord(request_id="test_2", timestamp="now", model="test")
    sr = make_selection_result(before=50000, after=28000, hist_drop=5, mem_drop=10,
                                tools_drop=2, ret_drop=3)
    _add_budget_telemetry(r, selection_result=sr)
    assert r.budget_enabled is True
    assert r.budget_before_tokens == 50000
    assert r.budget_after_tokens == 28000
    assert r.budget_drop_count == 20  # 5+10+2+3
    assert r.system_budget_exceeded is False
    print(f"  ✓ test_2: fields populated: before={r.budget_before_tokens}, "
          f"after={r.budget_after_tokens}, drop={r.budget_drop_count}")


def test_3_to_dict_includes_budget():
    """to_dict() must include all budget telemetry fields."""
    from captn.runtime.hermes_profiler import ProfileRecord, _add_budget_telemetry
    r = ProfileRecord(request_id="test_3", timestamp="now", model="test")
    sr = make_selection_result(before=30000, after=25000)
    _add_budget_telemetry(r, selection_result=sr)
    d = r.to_dict()
    assert "budget_enabled" in d
    assert "budget_before_tokens" in d
    assert "budget_after_tokens" in d
    assert "budget_drop_count" in d
    assert "budget_warnings" in d
    assert "system_budget_exceeded" in d
    assert d["budget_enabled"] is True
    assert d["budget_before_tokens"] == 30000
    assert d["budget_after_tokens"] == 25000
    print(f"  ✓ test_3: to_dict() has {len(d)} keys including 6 budget fields")


def test_4_fields_includes_budget():
    """fields() must include budget telemetry columns."""
    from captn.runtime.hermes_profiler import ProfileRecord
    f = ProfileRecord.fields()
    assert "budget_enabled" in f
    assert "budget_before_tokens" in f
    assert "budget_after_tokens" in f
    assert "budget_drop_count" in f
    assert "budget_warnings" in f
    assert "system_budget_exceeded" in f
    # Must appear after cache fields and before oversized_items
    bi = f.index("budget_enabled")
    ci = f.index("cache_write_tokens")
    oi = f.index("oversized_items")
    assert bi > ci, f"budget_enabled at {bi} should be after cache_write at {ci}"
    assert bi < oi, f"budget_enabled at {bi} should be before oversized at {oi}"
    print(f"  ✓ test_4: {len(f)} fields including 6 budget columns")


def test_5_budget_summary_with_records():
    """budget_summary must aggregate budget records correctly."""
    from captn.runtime.hermes_profiler import ProfileRecord, _add_budget_telemetry, budget_summary, clear_records, _append_record, records_summary
    clear_records()
    # Add 2 budget records — use explicit drop=0 for non-history
    r1 = ProfileRecord(request_id="r1", timestamp="now1", model="test")
    _add_budget_telemetry(r1, selection_result=make_selection_result(
        before=50000, after=25000, hist_drop=10, mem_drop=0, tools_drop=0, ret_drop=0))
    _append_record(r1)
    r2 = ProfileRecord(request_id="r2", timestamp="now2", model="test")
    _add_budget_telemetry(r2, selection_result=make_selection_result(
        before=40000, after=20000, hist_drop=5, mem_drop=0, tools_drop=0, ret_drop=0))
    _append_record(r2)
    # Add one non-budget record
    r3 = ProfileRecord(request_id="r3", timestamp="now3", model="test")
    r3.estimated_total = 1000
    _append_record(r3)

    summary = budget_summary()
    assert summary["enabled_requests"] == 2
    assert summary["total_before"] == 90000  # 50000 + 40000
    assert summary["total_after"] == 45000   # 25000 + 20000
    assert summary["total_items_dropped"] == 15  # 10 + 5
    assert summary["avg_reduction_pct"] > 0

    # Verify records_summary also has budget section
    rs = records_summary()
    assert rs.get("budget", {}).get("enabled_requests") == 2

    clear_records()
    print(f"  ✓ test_5: budget_summary: {summary['enabled_requests']} records, "
          f"{summary['total_before']}->{summary['total_after']} tokens, "
          f"{summary['total_items_dropped']} dropped")


def test_6_budget_summary_empty():
    """budget_summary must return empty when no budget records."""
    from captn.runtime.hermes_profiler import budget_summary, clear_records
    clear_records()
    summary = budget_summary()
    assert summary["enabled_requests"] == 0
    print(f"  ✓ test_6: empty summary: enabled_requests={summary['enabled_requests']}")


def test_7_records_summary_budget_aggregates():
    """records_summary must include budget section."""
    from captn.runtime.hermes_profiler import records_summary, clear_records, _append_record, ProfileRecord, _add_budget_telemetry
    clear_records()
    r = ProfileRecord(request_id="test_7", timestamp="now", model="test", estimated_total=50000)
    from captn.runtime.context_budget import SelectionResult, BudgetConfig
    config = BudgetConfig()
    sr = SelectionResult(
        original_system_tokens=10000, original_history_tokens=20000,
        original_memory_tokens=10000, original_tool_tokens=5000,
        original_retrieved_tokens=5000, original_total_tokens=50000,
        selected_system_tokens=5000, selected_history_tokens=10000,
        selected_memory_tokens=5000, selected_tool_tokens=3000,
        selected_retrieved_tokens=3000, selected_total_tokens=28000,
        budget_total=config.total, budget_system=config.system,
        budget_history=config.history, budget_memory=config.memory,
        budget_tools=config.tools, budget_retrieved=config.retrieved,
        system_budget_exceeded=False, tools_budget_exceeded=False,
        total_budget_exceeded=False,
        history_items_dropped=5, memory_items_dropped=10,
        retrieved_items_dropped=3, tools_dropped=2,
        decisions=[], selected_messages=[], selected_tools=[],
        selected_memory_text="", selected_retrieved_text="",
        selected_system_text="", history_compressed=False,
        history_compressed_savings=0, dry_run=True,
    )
    _add_budget_telemetry(r, selection_result=sr)
    _append_record(r)
    rs = records_summary()
    assert "budget" in rs
    assert rs["budget"]["enabled_requests"] == 1
    assert rs["oversized_requests"] == 1  # estimated_total=50000 > 30000
    clear_records()
    print(f"  ✓ test_7: records_summary has budget section, {rs['budget']['enabled_requests']} enabled, {rs['oversized_requests']} oversized")


def test_8_jsonl_serialization():
    """JSONL serialization must include budget fields."""
    from captn.runtime.hermes_profiler import ProfileRecord, _add_budget_telemetry, clear_records
    clear_records()
    r = ProfileRecord(request_id="test_8", timestamp="now", model="test")
    _add_budget_telemetry(r, selection_result=make_selection_result(before=30000, after=20000))
    jsonl = r.to_jsonl()
    d = json.loads(jsonl)
    assert "budget_enabled" in d
    assert d["budget_before_tokens"] == 30000
    assert d["budget_after_tokens"] == 20000
    # Roundtrip: deserialize to dict
    from captn.runtime.hermes_profiler import ProfileRecord
    d2 = ProfileRecord(**d)
    assert d2.budget_enabled == d["budget_enabled"]
    assert d2.budget_before_tokens == d["budget_before_tokens"]
    print(f"  ✓ test_8: JSONL roundtrip OK ({len(jsonl)} chars)")


def test_9_clear_records():
    """Clear records must remove all records."""
    from captn.runtime.hermes_profiler import clear_records, _append_record, get_all_records, ProfileRecord, _add_budget_telemetry
    clear_records()
    r = ProfileRecord(request_id="test_9_clear", timestamp="now", model="test")
    _add_budget_telemetry(r, selection_result=make_selection_result(before=10000, after=8000))
    _append_record(r)
    assert len(get_all_records()) == 1
    n = clear_records()
    assert n == 1
    assert len(get_all_records()) == 0
    print(f"  ✓ test_9: cleared {n} records")


def test_10_warnings_accumulated():
    """Budget warnings must be accumulated when budget exceeded."""
    from captn.runtime.hermes_profiler import ProfileRecord, _add_budget_telemetry
    from captn.runtime.context_budget import SelectionResult, BudgetConfig
    config = BudgetConfig()
    r = ProfileRecord(request_id="test_10", timestamp="now", model="test")
    sr = SelectionResult(
        original_system_tokens=10000, original_history_tokens=20000,
        original_memory_tokens=10000, original_tool_tokens=5000,
        original_retrieved_tokens=5000, original_total_tokens=50000,
        selected_system_tokens=10000, selected_history_tokens=15000,
        selected_memory_tokens=10000, selected_tool_tokens=5000,
        selected_retrieved_tokens=5000, selected_total_tokens=50000,  # exceeds 30K
        budget_total=config.total, budget_system=config.system,
        budget_history=config.history, budget_memory=config.memory,
        budget_tools=config.tools, budget_retrieved=config.retrieved,
        system_budget_exceeded=True, tools_budget_exceeded=True,
        total_budget_exceeded=True,  # selected=50000 > 30000
        history_items_dropped=0, memory_items_dropped=0,
        retrieved_items_dropped=0, tools_dropped=0,
        decisions=[], selected_messages=[], selected_tools=[],
        selected_memory_text="", selected_retrieved_text="",
        selected_system_text="", history_compressed=False,
        history_compressed_savings=0, dry_run=True,
    )
    _add_budget_telemetry(r, selection_result=sr)
    assert len(r.budget_warnings) == 3, f"Expected 3 warnings, got {r.budget_warnings}"
    # overflow = selected_total_tokens - budget_total = 50000 - 30000 = 20000
    has_total = any("total_budget_exceeded=20000" in w for w in r.budget_warnings)
    has_sys = any("system_budget_exceeded" in w for w in r.budget_warnings)
    has_tools = any("tools_budget_exceeded" in w for w in r.budget_warnings)
    assert has_total, f"Missing total_budget_exceeded=20000 warning: {r.budget_warnings}"
    assert has_sys, f"Missing system_budget_exceeded warning: {r.budget_warnings}"
    assert has_tools, f"Missing tools_budget_exceeded warning: {r.budget_warnings}"
    print(f"  ✓ test_10: {len(r.budget_warnings)} warnings: {r.budget_warnings}")


def test_11_rollback_feature_flag():
    """Feature flag must work for rollback (no code change needed)."""
    from captn.runtime.context_budget import is_budget_enabled
    # Save env
    old = os.environ.get("CAPTN_CONTEXT_BUDGET")
    if "CAPTN_CONTEXT_BUDGET" in os.environ:
        del os.environ["CAPTN_CONTEXT_BUDGET"]
    assert is_budget_enabled() is False  # Default off
    os.environ["CAPTN_CONTEXT_BUDGET"] = "on"
    assert is_budget_enabled() is True
    os.environ["CAPTN_CONTEXT_BUDGET"] = "off"
    assert is_budget_enabled() is False  # Rollback via env var
    # Restore
    if old is not None:
        os.environ["CAPTN_CONTEXT_BUDGET"] = old
    else:
        os.environ.pop("CAPTN_CONTEXT_BUDGET", None)
    print(f"  ✓ test_11: rollback via CAPTN_CONTEXT_BUDGET=off works")


if __name__ == "__main__":
    print(f"\n{'='*60}")
    print(f"  BUDGET TELEMETRY TESTS")
    print(f"{'='*60}\n")

    tests = [
        test_1_budget_fields_exist,
        test_2_add_budget_telemetry_populates_fields,
        test_3_to_dict_includes_budget,
        test_4_fields_includes_budget,
        test_5_budget_summary_with_records,
        test_6_budget_summary_empty,
        test_7_records_summary_budget_aggregates,
        test_8_jsonl_serialization,
        test_9_clear_records,
        test_10_warnings_accumulated,
        test_11_rollback_feature_flag,
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