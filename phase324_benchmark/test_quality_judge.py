#!/usr/bin/env python3
"""
Phase 3.24 — Quality Judge Infrastructure Tests.

Covers:
  - Tool chain integrity (orphan tool result removal)
  - D.3.5 counterbalancing determinism + balance
  - Judge score parsing: VALID / MISSING / DEFAULTED / INCONSISTENT
  - Regression classification (NO_REGRESSION/MINOR/MAJOR/CRITICAL)
  - Paired bootstrap CI
  - Control/Treatment message assembly
  - ContextBudget application in treatment
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))

from phase324_benchmark.quality_judge import (
    _order_control_first,
    _parse_judge_scores,
    _ensure_tool_chain_integrity,
    _classify_regression,
    _paired_bootstrap_ci,
    _build_control_messages,
    _build_treatment_messages,
    _extract_query,
    _bucket,
)


# ═══════════════════════════════════════════════════════════════════
# 1. TOOL CHAIN INTEGRITY
# ═══════════════════════════════════════════════════════════════════

def test_orphan_tool_result_removed():
    """Tool result with no preceding assistant tool_call is removed."""
    msgs = [
        {"role": "user", "content": "hello"},
        {"role": "tool", "content": "result", "tool_call_id": "orphan_1"},
    ]
    out, n = _ensure_tool_chain_integrity(msgs)
    assert n == 1
    assert len(out) == 1
    assert out[0]["role"] == "user"


def test_valid_tool_chain_kept():
    """Tool result WITH matching assistant tool_call is kept."""
    msgs = [
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "x", "arguments": "{}"}}]},
        {"role": "tool", "content": "result", "tool_call_id": "call_1"},
    ]
    out, n = _ensure_tool_chain_integrity(msgs)
    assert n == 0
    assert len(out) == 2


def test_mixed_chain():
    """Valid tool result kept, orphan removed."""
    msgs = [
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "good", "type": "function", "function": {"name": "x", "arguments": "{}"}}]},
        {"role": "tool", "content": "result-good", "tool_call_id": "good"},
        {"role": "tool", "content": "result-bad", "tool_call_id": "orphan"},
    ]
    out, n = _ensure_tool_chain_integrity(msgs)
    assert n == 1
    assert len(out) == 2


# ═══════════════════════════════════════════════════════════════════
# 2. D.3.5 COUNTERBALANCING
# ═══════════════════════════════════════════════════════════════════

def test_counterbalance_deterministic():
    assert _order_control_first("req_001") == _order_control_first("req_001")


def test_counterbalance_not_python_hash():
    """Must NOT use python hash() (salted per process)."""
    h1 = hash("req_001")
    h2 = hash("req_001")
    # In same process python hash is stable, but D.3.5 requires md5 for cross-process
    o1 = _order_control_first("req_001")
    o2 = _order_control_first("req_001")
    assert o1 == o2
    # md5-based decision — verify it's the md5-based one
    h = hashlib.md5("req_001".encode()).hexdigest()
    assert o1 == ((int(h[:8], 16) % 2) == 0)


def test_counterbalance_balance():
    ids = [f"req_{i:06d}" for i in range(1000)]
    orders = [_order_control_first(sid) for sid in ids]
    ctrl_first = sum(1 for o in orders if o)
    pct = ctrl_first / 1000.0
    assert 0.40 <= pct <= 0.60, f"Off balance: {pct:.1%}"


# ═══════════════════════════════════════════════════════════════════
# 3. JUDGE PARSING
# ═══════════════════════════════════════════════════════════════════

def test_parse_empty_is_missing():
    r = _parse_judge_scores("")
    assert r["judge_validation_status"] == "MISSING"


def test_parse_both_valid():
    text = """Response A:
- correctness (0-4): 3
- overall (0-4): 3
Response B:
- correctness (0-4): 4
- overall (0-4): 4
WINNER = B
One-line rationale.
"""
    r = _parse_judge_scores(text)
    assert r["judge_validation_status"] == "VALID"
    assert r["control_overall"] == 3.0
    assert r["treatment_overall"] == 4.0
    assert r["quality_delta"] == 1.0
    assert r["judge_score_inconsistent"] is False


def test_parse_winner_contradicts():
    text = """Response A:
- overall (0-4): 4
Response B:
- overall (0-4): 1
WINNER = B
Rationale.
"""
    r = _parse_judge_scores(text)
    assert r["judge_validation_status"] == "VALID"
    assert r["judge_score_inconsistent"] is True
    assert r["judge_winner_consistent"] is False


def test_parse_one_side_missing_defaulted():
    text = """Response A:
- correctness (0-4): 3
- overall (0-4): 3
Response B:
- correctness (0-4): 0
WINNER = B
"""
    r = _parse_judge_scores(text)
    assert r["judge_validation_status"] == "DEFAULTED"
    assert r["judge_score_defaulted"] is True


def test_parse_no_scores_missing():
    r = _parse_judge_scores("WINNER = A\nSome rationale.\n")
    assert r["judge_validation_status"] == "MISSING"


# ═══════════════════════════════════════════════════════════════════
# 4. REGRESSION CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════

def test_classify():
    assert _classify_regression(0.0) == "NO_REGRESSION"
    assert _classify_regression(-0.5) == "NO_REGRESSION"
    assert _classify_regression(-0.75) == "MINOR"
    assert _classify_regression(-1.5) == "MINOR"
    assert _classify_regression(-1.6) == "MAJOR"
    assert _classify_regression(-2.5) == "MAJOR"
    assert _classify_regression(-2.6) == "CRITICAL"
    assert _classify_regression(None) == "UNKNOWN"


# ═══════════════════════════════════════════════════════════════════
# 5. BOOTSTRAP CI
# ═══════════════════════════════════════════════════════════════════

def test_bootstrap_ci():
    deltas = [0.1, -0.2, 0.0, 0.3, -0.1, 0.2, -0.05, 0.15, 0.0, -0.15] * 10
    ci = _paired_bootstrap_ci(deltas, n_resamples=2000)
    assert ci["mean"] != float("nan")
    assert ci["lower"] <= ci["mean"] <= ci["upper"]
    assert ci["includes_zero"] is True


def test_bootstrap_ci_negative():
    deltas = [-3.0, -2.8, -3.1, -2.9, -3.2, -3.0] * 10
    ci = _paired_bootstrap_ci(deltas, n_resamples=2000)
    assert ci["upper"] < 0
    assert ci["includes_zero"] is False


# ═══════════════════════════════════════════════════════════════════
# 6. MESSAGE ASSEMBLY
# ═══════════════════════════════════════════════════════════════════

def _make_scenario() -> dict:
    return {
        "id": "req_323_30K_000",
        "family": "long_coding_conversation",
        "domain_hint": "software",
        "system_prompt": "You are CaptN-BRAIN.",
        "messages": [
            {"role": "user", "content": "Step 1: implement API"},
            {"role": "assistant", "content": "Done. Here's the code."},
            {"role": "tool", "content": "result", "tool_call_id": "orphan_1"},
            {"role": "user", "content": "Final question: summarize the work?"},
        ],
        "tools": [{"type": "function", "function": {"name": "read_file", "description": "Read a file"}}],
        "memory_text": "User prefers Python.",
        "retrieved_context": "Retrieved doc.",
    }


def test_extract_query():
    history, query = _extract_query(_make_scenario())
    assert query == "Final question: summarize the work?"
    assert len(history) == 3
    assert history[-1]["role"] == "tool"  # orphan stays in history — cleaned later


def test_control_messages():
    msgs, tools = _build_control_messages(_make_scenario())
    # Orphan tool result removed
    assert all(m.get("role") != "tool" for m in msgs)
    assert msgs[0]["role"] == "system"
    assert msgs[-1]["role"] == "user"
    assert "Question: Final question" in msgs[-1]["content"]
    assert len(tools) == 1


def test_bucket():
    assert _bucket(5000) == "<10K"
    assert _bucket(15000) == "10-20K"
    assert _bucket(25000) == "20-30K"
    assert _bucket(40000) == "30-50K"
    assert _bucket(60000) == ">50K"


def test_treatment_reduces_tokens():
    """CaptN treatment should produce fewer tokens than control for large scenario >30K."""
    from phase324_benchmark.quality_judge import _estimate_msgs_tokens
    # Build a scenario large enough to trigger budget enforcement
    big_history = []
    for i in range(50):
        big_history.append({"role": "user", "content": "Long user message " + "x" * 2000 + f" round {i}"})
        big_history.append({"role": "assistant", "content": "Long assistant response " + "y" * 2000 + f" round {i}"})
    big_history.append({"role": "user", "content": "Final question: summarize the entire conversation?"})
    scenario = {
        "id": "req_323_30K_001",
        "family": "long_coding_conversation",
        "domain_hint": "software",
        "system_prompt": "You are CaptN-BRAIN. " * 200 + "Your job is to help with coding.",
        "messages": big_history,
        "tools": [
            {"type": "function", "function": {"name": f"tool_{i}", "description": "d" * 200,
                "parameters": {"type": "object", "properties": {
                    "input": {"type": "string"},
                    "options": {"type": "object", "additionalProperties": True}
                }, "required": ["input"]}}}
            for i in range(40)
        ],
        "memory_text": "",
        "retrieved_context": "",
    }
    c_msgs, c_tools = _build_control_messages(scenario)
    t_msgs, t_tools = _build_treatment_messages(scenario)
    c_tok = _estimate_msgs_tokens(c_msgs, c_tools)
    t_tok = _estimate_msgs_tokens(t_msgs, t_tools)
    print(f"  Control tokens: {c_tok}, Treatment tokens: {t_tok}")
    assert t_tok < c_tok, f"Treatment ({t_tok}) >= Control ({c_tok}) — budget may not be enforced"
    assert len(t_tools) <= len(c_tools)


if __name__ == "__main__":
    import traceback
    passed = 0
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
                passed += 1
            except Exception as e:
                print(f"FAIL {name}: {e}")
                traceback.print_exc()
                failed += 1
    print(f"\n{'='*40}")
    print(f"TESTS: {passed}/{passed+failed}")
    sys.exit(1 if failed else 0)