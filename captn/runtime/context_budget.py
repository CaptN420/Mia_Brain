#!/usr/bin/env python3
"""
context_budget.py — Production Context Budget with relevance-based selection.

Prevents oversized prompts by selecting the most relevant content within
a configurable token budget. Uses model-specific token estimation.

Architecture::

    Hermes assembles candidates
            ↓
    CaptN Context Budget
            ↓
    selection/compression
            ↓
    final api_kwargs
            ↓
    existing pre_api_request profiler
            ↓
    OpenRouter

Categories:
  - System Prompt     (max 5,000 tokens)  — immutable core never removed
  - Conversation Hist (max 8,000 tokens)  — recent + compressed older
  - Long-Term Memory  (max 7,000 tokens)  — relevance-scored
  - Tools / Schemas   (max 5,000 tokens)  — utility/token_cost selection
  - Retrieved Context (max 5,000 tokens)  — BM25 + token-cost top-K

Feature flag: CAPTN_CONTEXT_BUDGET=on  (default: off)

Usage:
    from captn.runtime.context_budget import (
        ContextBudget, BudgetConfig, SelectionResult,
        estimate_provider_tokens, DEFAULT_BUDGET_CONFIG,
    )

    budget = ContextBudget(model="deepseek/deepseek-v4-flash")
    result = budget.select(
        system_prompt="...",
        messages=[...],
        memory_text="...",
        tools=[...],
        retrieved_context="...",
        user_query="what is gcd?",
    )
    print(result.report())
    # result.selected_messages, result.selected_tools, etc.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("context_budget")

# ═══════════════════════════════════════════════════════════════════
# TOKEN ESTIMATOR — model-specific
# ═══════════════════════════════════════════════════════════════════

_deepseek_tokenizer: Optional[Any] = None
try:
    import requests
    r = requests.get(
        "https://huggingface.co/deepseek-ai/deepseek-v4-flash/raw/main/tokenizer.json",
        timeout=10,
    )
    if r.status_code == 200:
        from tokenizers import Tokenizer as _Tok
        _deepseek_tokenizer = _Tok.from_str(r.text)
except Exception:
    pass

_tiktoken_available = False
try:
    import tiktoken
    _tiktoken_available = True
except ImportError:
    pass


def estimate_provider_tokens(text: str, model: str = "") -> int:
    """Estimate provider-equivalent tokens for a text string.

    Uses the best available model-specific tokenizer.
    Returns an integer token count.
    """
    if not text:
        return 0
    ml = model.lower()
    if _deepseek_tokenizer is not None and "deepseek" in ml:
        try:
            return len(_deepseek_tokenizer.encode(text))
        except Exception:
            pass
    if _tiktoken_available:
        try:
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except Exception:
            pass
    return max(1, (len(text) + 3) // 4)


def estimate_messages_tokens(messages: List[Dict[str, Any]], model: str = "") -> int:
    """Estimate tokens for a list of OpenAI-format messages."""
    if not messages:
        return 0
    total = 0
    for msg in messages:
        total += 4  # role overhead
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_provider_tokens(content, model)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    total += estimate_provider_tokens(part.get("text", ""), model)
        if "tool_calls" in msg:
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                total += estimate_provider_tokens(fn.get("name", ""), model)
                total += estimate_provider_tokens(fn.get("arguments", ""), model)
    return total


def estimate_tool_tokens(tools: List[Dict[str, Any]], model: str = "") -> int:
    """Estimate tokens for a list of tool schemas."""
    if not tools:
        return 0
    return estimate_provider_tokens(json.dumps(tools, ensure_ascii=False, default=str), model)


# ═══════════════════════════════════════════════════════════════════
# BUDGET CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

@dataclass
class BudgetConfig:
    """Context Budget configuration.

    All values are in provider-equivalent tokens.
    """
    total: int = 30_000
    system: int = 5_000
    history: int = 8_000
    memory: int = 7_000
    tools: int = 5_000
    retrieved: int = 5_000

    @classmethod
    def from_env(cls) -> "BudgetConfig":
        """Load from environment variables."""
        return cls(
            total=int(os.environ.get("CAPTN_CONTEXT_BUDGET_TOTAL", "30000")),
            system=int(os.environ.get("CAPTN_CONTEXT_BUDGET_SYSTEM", "5000")),
            history=int(os.environ.get("CAPTN_CONTEXT_BUDGET_HISTORY", "8000")),
            memory=int(os.environ.get("CAPTN_CONTEXT_BUDGET_MEMORY", "7000")),
            tools=int(os.environ.get("CAPTN_CONTEXT_BUDGET_TOOLS", "5000")),
            retrieved=int(os.environ.get("CAPTN_CONTEXT_BUDGET_RETRIEVED", "5000")),
        )


DEFAULT_BUDGET_CONFIG = BudgetConfig()


# ═══════════════════════════════════════════════════════════════════
# SELECTION RESULT
# ═══════════════════════════════════════════════════════════════════

@dataclass
class SelectionDecision:
    """Record of one selection/pruning decision."""
    category: str
    item_id: str = ""
    estimated_tokens: int = 0
    relevance_score: float = 0.0
    selected: bool = False
    selection_reason: str = ""


@dataclass
class SelectionResult:
    """Result of applying the Context Budget to a request."""
    # Original estimates
    original_system_tokens: int = 0
    original_history_tokens: int = 0
    original_memory_tokens: int = 0
    original_tool_tokens: int = 0
    original_retrieved_tokens: int = 0
    original_total_tokens: int = 0

    # Selected counts
    selected_system_tokens: int = 0
    selected_history_tokens: int = 0
    selected_memory_tokens: int = 0
    selected_tool_tokens: int = 0
    selected_retrieved_tokens: int = 0
    selected_total_tokens: int = 0

    # Budget limits
    budget_total: int = 30_000
    budget_system: int = 5_000
    budget_history: int = 8_000
    budget_memory: int = 7_000
    budget_tools: int = 5_000
    budget_retrieved: int = 5_000

    # Overflow flags
    system_budget_exceeded: bool = False
    tools_budget_exceeded: bool = False
    total_budget_exceeded: bool = False

    # Items dropped
    history_items_dropped: int = 0
    memory_items_dropped: int = 0
    retrieved_items_dropped: int = 0
    tools_dropped: int = 0

    # Decisions
    decisions: List[SelectionDecision] = field(default_factory=list)

    # Selected content (for actual use)
    selected_messages: List[Dict[str, Any]] = field(default_factory=list)
    selected_tools: List[Dict[str, Any]] = field(default_factory=list)
    selected_memory_text: str = ""
    selected_retrieved_text: str = ""
    selected_system_text: str = ""

    # History compression
    history_compressed: bool = False
    history_compressed_savings: int = 0

    # Dry-run mode
    dry_run: bool = False

    @property
    def unused_budget(self) -> int:
        return max(0, self.budget_total - self.selected_total_tokens)

    @property
    def overflow_tokens(self) -> int:
        if self.total_budget_exceeded:
            return self.selected_total_tokens - self.budget_total
        return 0

    def report(self) -> str:
        sep = "─" * 52
        lines = [sep, "  HERMES CONTEXT BUDGET", sep]
        lines.append(f"  {'Budget':30s} {self.budget_total:>8,d}")
        lines.append("")
        lines.append(f"  {'System':30s} {self.selected_system_tokens:>6,d} / {self.budget_system:<6,d}  "
                      f"{'(!) EXCEEDED' if self.system_budget_exceeded else ''}")
        lines.append(f"  {'History':30s} {self.selected_history_tokens:>6,d} / {self.budget_history:<6,d}  "
                      f"{'compressed' if self.history_compressed else ''}")
        lines.append(f"  {'Memory':30s} {self.selected_memory_tokens:>6,d} / {self.budget_memory:<6,d}")
        lines.append(f"  {'Tools':30s} {self.selected_tool_tokens:>6,d} / {self.budget_tools:<6,d}  "
                      f"{'(!) EXCEEDED' if self.tools_budget_exceeded else ''}")
        lines.append(f"  {'Retrieved':30s} {self.selected_retrieved_tokens:>6,d} / {self.budget_retrieved:<6,d}")
        lines.append(sep)
        lines.append(f"  {'Selected Total':30s} {self.selected_total_tokens:>8,d}")
        lines.append(f"  {'Original Total':30s} {self.original_total_tokens:>8,d}")
        if self.selected_total_tokens < self.original_total_tokens:
            saved = self.original_total_tokens - self.selected_total_tokens
            pct = (saved / max(self.original_total_tokens, 1)) * 100
            lines.append(f"  {'Token Reduction':30s} {saved:>8,d}  ({pct:.0f}%)")
        lines.append(f"  {'Unused budget':30s} {self.unused_budget:>8,d}")
        if self.overflow_tokens > 0:
            lines.append(f"  {'⚠️  OVERFLOW':30s} {self.overflow_tokens:>+8,d}")
        lines.append("")
        lines.append(f"  {'History items dropped':30s} {self.history_items_dropped:>8,d}")
        lines.append(f"  {'Memory items dropped':30s} {self.memory_items_dropped:>8,d}")
        lines.append(f"  {'Retrieved items dropped':30s} {self.retrieved_items_dropped:>8,d}")
        lines.append(f"  {'Tools dropped':30s} {self.tools_dropped:>8,d}")
        if self.dry_run:
            lines.append(sep)
            lines.append("  DRY RUN — no changes applied to real request")
        lines.append(sep)
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "budget": {
                "total": self.budget_total,
                "system": self.budget_system, "history": self.budget_history,
                "memory": self.budget_memory, "tools": self.budget_tools,
                "retrieved": self.budget_retrieved,
            },
            "original": {
                "system": self.original_system_tokens, "history": self.original_history_tokens,
                "memory": self.original_memory_tokens, "tools": self.original_tool_tokens,
                "retrieved": self.original_retrieved_tokens, "total": self.original_total_tokens,
            },
            "selected": {
                "system": self.selected_system_tokens, "history": self.selected_history_tokens,
                "memory": self.selected_memory_tokens, "tools": self.selected_tool_tokens,
                "retrieved": self.selected_retrieved_tokens, "total": self.selected_total_tokens,
            },
            "overflow": {
                "system_budget_exceeded": self.system_budget_exceeded,
                "tools_budget_exceeded": self.tools_budget_exceeded,
                "total_budget_exceeded": self.total_budget_exceeded,
                "overflow_tokens": self.overflow_tokens,
            },
            "dropped": {
                "history_items": self.history_items_dropped,
                "memory_items": self.memory_items_dropped,
                "retrieved_items": self.retrieved_items_dropped,
                "tools": self.tools_dropped,
            },
            "history_compressed": self.history_compressed,
            "history_compressed_savings": self.history_compressed_savings,
            "dry_run": self.dry_run,
            "unused_budget": self.unused_budget,
        }


# ═══════════════════════════════════════════════════════════════════
# CATEGORY SELECTORS
# ═══════════════════════════════════════════════════════════════════

def _select_system(
    system_prompt: str,
    budget: int,
    model: str = "",
) -> Tuple[str, int, bool, List[SelectionDecision]]:
    """Select system prompt content within budget.

    Core system instructions are NEVER removed. Only optional sections
    (skills, ephemeral context, dynamic metadata) are reduced.
    """
    decisions: List[SelectionDecision] = []
    total_tok = estimate_provider_tokens(system_prompt, model)

    if total_tok <= budget:
        # Within budget — keep everything
        decisions.append(SelectionDecision(
            category="system", item_id="system_prompt",
            estimated_tokens=total_tok, relevance_score=1.0,
            selected=True, selection_reason="within budget",
        ))
        return system_prompt, total_tok, False, decisions

    # Try to identify optional sections within the system prompt
    # Skills block: <available_skills>...</available_skills>
    skills_match = re.search(r"<available_skills>.*?</available_skills>", system_prompt, re.DOTALL)
    skills_text = skills_match.group(0) if skills_match else ""
    skills_tok = estimate_provider_tokens(skills_text, model) if skills_text else 0

    # Core system = everything minus skills
    core_text = system_prompt.replace(skills_text, "") if skills_text else system_prompt
    core_tok = estimate_provider_tokens(core_text, model)

    if core_tok > budget:
        # Even the immutable core exceeds budget — flag and keep everything
        decisions.append(SelectionDecision(
            category="system", item_id="core",
            estimated_tokens=core_tok, relevance_score=1.0,
            selected=True,
            selection_reason="MANDATORY — core exceeds budget, kept intact",
        ))
        return system_prompt, total_tok, True, decisions

    # Core fits; try to fit skills
    if core_tok + skills_tok <= budget and skills_text:
        decisions.append(SelectionDecision(
            category="system", item_id="skills",
            estimated_tokens=skills_tok, relevance_score=0.7,
            selected=True, selection_reason="fits after core",
        ))
        return system_prompt, total_tok, False, decisions

    # Drop skills
    selected_text = core_text
    selected_tok = core_tok
    if skills_text:
        decisions.append(SelectionDecision(
            category="system", item_id="skills",
            estimated_tokens=skills_tok, relevance_score=0.7,
            selected=False, selection_reason="dropped to fit budget",
        ))

    return selected_text, selected_tok, False, decisions


def _select_history(
    messages: List[Dict[str, Any]],
    budget: int,
    model: str = "",
    user_query: str = "",
) -> Tuple[List[Dict[str, Any]], int, bool, int, int, List[SelectionDecision]]:
    """Select conversation history within budget.

    Priority:
    1. System message (always kept)
    2. Current user message (always kept)
    3. Recent turns (highest priority)
    4. Turns relevant to current query
    5. Compression of older content
    """
    decisions: List[SelectionDecision] = []
    if not messages:
        return [], 0, False, 0, 0, decisions

    # Separate system message (must keep)
    system_msg = None
    non_system: List[Dict[str, Any]] = []
    for m in messages:
        if m.get("role") == "system" and system_msg is None:
            system_msg = m
        else:
            non_system.append(m)

    sys_tok = estimate_messages_tokens([system_msg], model) if system_msg else 0
    remaining_budget = budget - sys_tok

    if remaining_budget <= 0 and system_msg:
        decisions.append(SelectionDecision(
            category="history", item_id="system",
            estimated_tokens=sys_tok, relevance_score=1.0,
            selected=True, selection_reason="MANDATORY — kept even if exceeds budget",
        ))
        return [system_msg], sys_tok, False, 0, 0, decisions

    if not non_system:
        selected = [system_msg] if system_msg else []
        return selected, sys_tok, False, 0, 0, decisions

    # Score each non-system message by recency + relevance
    # Current user message is the last user message
    last_user_idx = -1
    for i in range(len(non_system) - 1, -1, -1):
        if non_system[i].get("role") == "user":
            last_user_idx = i
            break

    scored: List[Tuple[float, int, Dict[str, Any]]] = []  # (score, idx, msg)
    for i, msg in enumerate(non_system):
        role = msg.get("role", "")
        content = msg.get("content", "")

        # Base score: recency (index / total)
        base = (i / max(len(non_system), 1)) * 0.6

        # Current user message gets max score
        if i == last_user_idx:
            base += 1.0

        # Relevance to query (simple keyword overlap)
        if user_query and isinstance(content, str):
            query_words = set(user_query.lower().split())
            content_words = set(content.lower().split())
            overlap = len(query_words & content_words)
            if overlap > 0:
                base += 0.3 * min(1.0, overlap / 5)

        # Tool results get a small boost if they're near the end
        if role == "tool" and i > len(non_system) - 5:
            base += 0.2

        tok = estimate_messages_tokens([msg], model)
        scored.append((base, i, msg))

    # Sort by score descending
    scored.sort(key=lambda x: -x[0])

    # Greedy selection within budget
    selected: List[Dict[str, Any]] = [system_msg] if system_msg else []
    selected_tok = sys_tok
    dropped = 0
    compressed = False
    compressed_savings = 0

    for score, idx, msg in scored:
        tok = estimate_messages_tokens([msg], model)
        if selected_tok + tok <= remaining_budget:
            selected.append(msg)
            selected_tok += tok
            decisions.append(SelectionDecision(
                category="history", item_id=f"msg_{idx}",
                estimated_tokens=tok, relevance_score=round(score, 3),
                selected=True, selection_reason=f"score={score:.3f}",
            ))
        else:
            dropped += 1
            decisions.append(SelectionDecision(
                category="history", item_id=f"msg_{idx}",
                estimated_tokens=tok, relevance_score=round(score, 3),
                selected=False, selection_reason=f"budget exceeded",
            ))

    # If we dropped a lot, try compressing the older content
    if dropped > 3 and len(non_system) > 6:
        try:
            from captn.runtime.history_compressor import compress_history
            # Compress dropped messages
            drop_msgs = [d for d in non_system if d not in selected]
            if drop_msgs:
                exchanges = [
                    {"role": m.get("role", "user"), "content": m.get("content", "")}
                    for m in drop_msgs
                ]
                summary = compress_history(exchanges)
                summary_text = summary.render(max_lines=20)
                summary_tok = estimate_provider_tokens(summary_text, model)

                if summary_tok > 0 and summary_tok < estimate_messages_tokens(drop_msgs, model):
                    # Try to add compressed summary
                    if selected_tok + summary_tok <= remaining_budget:
                        compressed_msg = {"role": "user", "content": f"[Compressed History]\n{summary_text}"}
                        selected.append(compressed_msg)
                        selected_tok += summary_tok
                        compressed = True
                        compressed_savings = estimate_messages_tokens(drop_msgs, model) - summary_tok
                        dropped = len(drop_msgs)  # count as all dropped (replaced by summary)
                        decisions.append(SelectionDecision(
                            category="history", item_id="compressed_summary",
                            estimated_tokens=summary_tok, relevance_score=0.5,
                            selected=True, selection_reason="compressed older history",
                        ))
        except Exception:
            pass

    return selected, selected_tok, compressed, dropped, compressed_savings, decisions


def _select_memory(
    memory_text: str,
    budget: int,
    model: str = "",
    user_query: str = "",
) -> Tuple[str, int, int, List[SelectionDecision]]:
    """Select memory entries within budget using relevance scoring."""
    decisions: List[SelectionDecision] = []
    if not memory_text:
        return "", 0, 0, decisions

    total_tok = estimate_provider_tokens(memory_text, model)
    if total_tok <= budget:
        decisions.append(SelectionDecision(
            category="memory", item_id="all_memory",
            estimated_tokens=total_tok, relevance_score=1.0,
            selected=True, selection_reason="within budget",
        ))
        return memory_text, total_tok, 0, decisions

    # Split into lines/memories
    lines = memory_text.split("\n")
    lines = [l for l in lines if l.strip()]

    # Score each line by relevance to query
    scored: List[Tuple[float, str, int]] = []
    if user_query:
        query_words = set(user_query.lower().split())
        for line in lines:
            words = set(line.lower().split())
            overlap = len(query_words & words)
            score = (overlap / max(len(query_words), 1)) * 0.7 + 0.3  # 0.3 base + overlap
            tok = estimate_provider_tokens(line, model)
            scored.append((score, line, tok))
    else:
        for line in lines:
            tok = estimate_provider_tokens(line, model)
            scored.append((0.5, line, tok))

    scored.sort(key=lambda x: -x[0])

    # Greedy selection
    selected_lines: List[str] = []
    selected_tok = 0
    dropped = 0

    for score, line, tok in scored:
        if selected_tok + tok <= budget:
            selected_lines.append(line)
            selected_tok += tok
            decisions.append(SelectionDecision(
                category="memory", item_id=hashlib.md5(line.encode()).hexdigest()[:8],
                estimated_tokens=tok, relevance_score=round(score, 3),
                selected=True, selection_reason=f"score={score:.3f}",
            ))
        else:
            dropped += 1
            # Don't add decision for every dropped line to avoid spam

    selected_text = "\n".join(selected_lines)
    return selected_text, selected_tok, dropped, decisions


def _select_tools(
    tools: List[Dict[str, Any]],
    budget: int,
    model: str = "",
    user_query: str = "",
    domain_hint: str = "",
) -> Tuple[List[Dict[str, Any]], int, bool, int, List[SelectionDecision]]:
    """Select tools within budget using utility/token_cost ratio.

    Priority:
    1. Mandatory tools (none are truly mandatory at the CaptN layer)
    2. Domain-relevant tools (from domain filtering)
    3. Tools with high utility/cost ratio
    """
    decisions: List[SelectionDecision] = []
    if not tools:
        return [], 0, False, 0, decisions

    # Pre-filter by domain if available
    if domain_hint:
        try:
            from captn.runtime._tool_domains import DOMAIN_KEYWORDS
            domain_kws = DOMAIN_KEYWORDS.get(domain_hint, set())
            filtered = []
            for t in tools:
                fn = t.get("function", t) if isinstance(t, dict) else {}
                name = fn.get("name", "") if isinstance(fn, dict) else ""
                # Keep if domain-relevant (name matches domain keywords)
                if any(kw in name.lower() for kw in domain_kws):
                    filtered.append(t)
            if filtered:
                tools = filtered
        except Exception:
            pass

    # Measure each tool
    tool_scores: List[Tuple[float, Dict[str, Any], str, int]] = []
    for t in tools:
        fn = t.get("function", t) if isinstance(t, dict) else {}
        name = fn.get("name", str(t)[:40]) if isinstance(fn, dict) else str(t)[:40]
        tok = estimate_tool_tokens([t], model)

        # Utility score based on various factors
        utility = 0.5  # base

        # Domain relevance boost
        if domain_hint and isinstance(name, str):
            if domain_hint in name.lower():
                utility += 0.3

        # Query relevance
        if user_query and isinstance(name, str):
            query_words = set(user_query.lower().split())
            if any(qw in name.lower() for qw in query_words):
                utility += 0.4

        # MCP tools get a small boost (they're configured tools)
        if isinstance(name, str) and name.startswith("mcp_"):
            utility += 0.2

        # Delegation tools are important
        if isinstance(name, str) and name == "delegate_task":
            utility += 0.5

        # Utility/cost ratio
        cost_ratio = utility / max(tok, 1)
        tool_scores.append((cost_ratio, t, name, tok))

    tool_scores.sort(key=lambda x: -x[0])

    # Greedy selection
    selected_tools: List[Dict[str, Any]] = []
    selected_tok = 0
    dropped = 0
    exceeded = False

    for cost_ratio, t, name, tok in tool_scores:
        if selected_tok + tok <= budget:
            selected_tools.append(t)
            selected_tok += tok
            decisions.append(SelectionDecision(
                category="tools", item_id=name,
                estimated_tokens=tok, relevance_score=round(cost_ratio, 3),
                selected=True, selection_reason=f"cost_ratio={cost_ratio:.3f}",
            ))
        else:
            dropped += 1

    # If we dropped everything and the smallest tool doesn't fit
    if not selected_tools and tool_scores:
        # Keep the most important tool even if it exceeds budget
        best = tool_scores[0]
        selected_tools.append(best[1])
        selected_tok = best[3]
        exceeded = True
        decisions.append(SelectionDecision(
            category="tools", item_id=best[2],
            estimated_tokens=best[3], relevance_score=round(best[0], 3),
            selected=True, selection_reason="MANDATORY — single tool exceeds budget, kept",
        ))

    return selected_tools, selected_tok, exceeded, dropped, decisions


def _select_retrieved(
    retrieved_text: str,
    budget: int,
    model: str = "",
    user_query: str = "",
) -> Tuple[str, int, int, List[SelectionDecision]]:
    """Select retrieved context fragments within budget."""
    decisions: List[SelectionDecision] = []
    if not retrieved_text:
        return "", 0, 0, decisions

    total_tok = estimate_provider_tokens(retrieved_text, model)
    if total_tok <= budget:
        decisions.append(SelectionDecision(
            category="retrieved", item_id="all_retrieved",
            estimated_tokens=total_tok, relevance_score=1.0,
            selected=True, selection_reason="within budget",
        ))
        return retrieved_text, total_tok, 0, decisions

    # Split into fragments (by double newline or known fragment boundaries)
    fragments = re.split(r"\n\s*\n", retrieved_text)
    fragments = [f.strip() for f in fragments if f.strip()]

    if len(fragments) <= 1:
        # Single fragment — truncate
        truncated = retrieved_text[:int(budget * 4)]  # rough char estimate
        decisions.append(SelectionDecision(
            category="retrieved", item_id="truncated",
            estimated_tokens=budget, relevance_score=0.5,
            selected=True, selection_reason="truncated to fit budget",
        ))
        return truncated, budget, 0, decisions

    # Score fragments by relevance to query
    scored: List[Tuple[float, str, int]] = []
    if user_query:
        query_words = set(user_query.lower().split())
        for frag in fragments:
            words = set(frag.lower().split())
            overlap = len(query_words & words)
            score = (overlap / max(len(query_words), 1)) * 0.7 + 0.3
            tok = estimate_provider_tokens(frag, model)
            scored.append((score, frag, tok))
    else:
        for frag in fragments:
            tok = estimate_provider_tokens(frag, model)
            scored.append((0.5, frag, tok))

    scored.sort(key=lambda x: -x[0])

    # Greedy selection
    selected_frags: List[str] = []
    selected_tok = 0
    dropped = 0

    for score, frag, tok in scored:
        if selected_tok + tok <= budget:
            selected_frags.append(frag)
            selected_tok += tok
            decisions.append(SelectionDecision(
                category="retrieved", item_id=hashlib.md5(frag.encode()).hexdigest()[:8],
                estimated_tokens=tok, relevance_score=round(score, 3),
                selected=True, selection_reason=f"score={score:.3f}",
            ))
        else:
            dropped += 1

    selected_text = "\n\n".join(selected_frags)
    return selected_text, selected_tok, dropped, decisions


# ═══════════════════════════════════════════════════════════════════
# CONTEXT BUDGET — main orchestrator
# ═══════════════════════════════════════════════════════════════════

ENV_BUDGET = "CAPTN_CONTEXT_BUDGET"


def is_budget_enabled() -> bool:
    """Check if the Context Budget is enabled."""
    raw = os.environ.get(ENV_BUDGET, "off").strip().lower()
    return raw in ("on", "1", "true", "yes", "fixed", "adaptive")


class ContextBudget:
    """Context Budget orchestrator.

    Selects the most relevant content within token budgets per category.
    Observes the total budget and enforces it at the end.

    Usage:
        budget = ContextBudget(model="deepseek/deepseek-v4-flash")
        result = budget.select(
            system_prompt=system,
            messages=messages,
            memory_text=memory,
            tools=tools,
            retrieved_context=retrieved,
            user_query="what is gcd?",
            dry_run=True,
        )
        print(result.report())
        # Use result.selected_messages, result.selected_tools for the real request
    """

    def __init__(
        self,
        model: str = "",
        config: Optional[BudgetConfig] = None,
    ):
        self.model = model
        self.config = config or BudgetConfig.from_env()

    def select(
        self,
        *,
        system_prompt: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
        memory_text: str = "",
        tools: Optional[List[Dict[str, Any]]] = None,
        retrieved_context: str = "",
        user_query: str = "",
        domain_hint: str = "",
        dry_run: bool = False,
    ) -> SelectionResult:
        """Select content within the budget.

        Args:
            system_prompt: The full system prompt text.
            messages: Full conversation history (including system message).
            memory_text: Long-term memory text.
            tools: Full tool schemas list.
            retrieved_context: Retrieved/BM25 context text.
            user_query: Current user query for relevance scoring.
            domain_hint: Domain hint for tool filtering.
            dry_run: If True, compute selections but don't modify real request.

        Returns:
            SelectionResult with selected content and decisions.
        """
        conf = self.config
        result = SelectionResult(
            budget_total=conf.total, budget_system=conf.system,
            budget_history=conf.history, budget_memory=conf.memory,
            budget_tools=conf.tools, budget_retrieved=conf.retrieved,
            dry_run=dry_run,
        )

        msgs = messages or []
        tools_list = tools or []

        # ── 1. Original estimates ──
        # System
        sys_tok = estimate_provider_tokens(system_prompt, self.model)
        result.original_system_tokens = sys_tok

        # History (messages)
        hist_tok = estimate_messages_tokens(msgs, self.model)
        result.original_history_tokens = hist_tok

        # Memory
        mem_tok = estimate_provider_tokens(memory_text, self.model)
        result.original_memory_tokens = mem_tok

        # Tools
        tool_tok = estimate_tool_tokens(tools_list, self.model)
        result.original_tool_tokens = tool_tok

        # Retrieved
        ret_tok = estimate_provider_tokens(retrieved_context, self.model)
        result.original_retrieved_tokens = ret_tok

        result.original_total_tokens = sys_tok + hist_tok + mem_tok + tool_tok + ret_tok

        # ── 2. System prompt selection ──
        sel_sys, sel_sys_tok, sys_exceeded, sys_decisions = _select_system(
            system_prompt, conf.system, self.model,
        )
        result.selected_system_text = sel_sys
        result.selected_system_tokens = sel_sys_tok
        result.system_budget_exceeded = sys_exceeded
        result.decisions.extend(sys_decisions)

        # ── 3. History selection ──
        sel_msgs, sel_hist_tok, hist_compressed, hist_dropped, hist_savings, hist_decisions = _select_history(
            msgs, conf.history, self.model, user_query,
        )
        result.selected_messages = sel_msgs
        result.selected_history_tokens = sel_hist_tok
        result.history_compressed = hist_compressed
        result.history_compressed_savings = hist_savings
        result.history_items_dropped = hist_dropped
        result.decisions.extend(hist_decisions)

        # ── 4. Memory selection ──
        sel_mem, sel_mem_tok, mem_dropped, mem_decisions = _select_memory(
            memory_text, conf.memory, self.model, user_query,
        )
        result.selected_memory_text = sel_mem
        result.selected_memory_tokens = sel_mem_tok
        result.memory_items_dropped = mem_dropped
        result.decisions.extend(mem_decisions)

        # ── 5. Tool selection ──
        sel_tools, sel_tool_tok, tools_exceeded, tools_dropped, tool_decisions = _select_tools(
            tools_list, conf.tools, self.model, user_query, domain_hint,
        )
        result.selected_tools = sel_tools
        result.selected_tool_tokens = sel_tool_tok
        result.tools_budget_exceeded = tools_exceeded
        result.tools_dropped = tools_dropped
        result.decisions.extend(tool_decisions)

        # ── 6. Retrieved context selection ──
        sel_ret, sel_ret_tok, ret_dropped, ret_decisions = _select_retrieved(
            retrieved_context, conf.retrieved, self.model, user_query,
        )
        result.selected_retrieved_text = sel_ret
        result.selected_retrieved_tokens = sel_ret_tok
        result.retrieved_items_dropped = ret_dropped
        result.decisions.extend(ret_decisions)

        # ── 7. Total budget enforcement ──
        result.selected_total_tokens = (
            sel_sys_tok + sel_hist_tok + sel_mem_tok + sel_tool_tok + sel_ret_tok
        )

        if result.selected_total_tokens > conf.total:
            result.total_budget_exceeded = True
            logger.warning(
                "Context Budget exceeded: %d > %d. "
                "System=%d History=%d Memory=%d Tools=%d Retrieved=%d",
                result.selected_total_tokens, conf.total,
                sel_sys_tok, sel_hist_tok, sel_mem_tok, sel_tool_tok, sel_ret_tok,
            )

        return result


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

def register_cli(subparsers) -> None:
    p = subparsers.add_parser(
        "budget",
        help="Context Budget — relevance-based selection within token limits",
    )
    sub = p.add_subparsers(dest="budget_cmd")

    demo_p = sub.add_parser("demo", help="Show budget on a demo payload")
    demo_p.add_argument("--dry-run", action="store_true", default=True,
                        help="Show selections without applying (default: true)")
    demo_p.set_defaults(func=_cmd_budget_demo)

    status_p = sub.add_parser("status", help="Show budget configuration")
    status_p.set_defaults(func=_cmd_budget_status)

    # ── Telemetry subcommand ─────────────────────────────────────
    tel_p = sub.add_parser(
        "telemetry",
        help="Budget telemetry — view aggregated records, per-request details, export",
    )
    tel_sub = tel_p.add_subparsers(dest="budget_telemetry_cmd")

    tel_records = tel_sub.add_parser("records", help="Show aggregated budget telemetry summary")
    tel_records.set_defaults(func=_cmd_budget_telemetry_records)

    tel_record = tel_sub.add_parser("record", help="Show details for a specific request")
    tel_record.add_argument("request_id", help="Request ID to inspect")
    tel_record.set_defaults(func=_cmd_budget_telemetry_record)

    tel_clear = tel_sub.add_parser("clear", help="Clear all telemetry records")
    tel_clear.set_defaults(func=_cmd_budget_telemetry_clear)

    tel_export = tel_sub.add_parser("export", help="Export telemetry records as JSONL")
    tel_export.add_argument("--output", "-o", default=None, help="Output file path (default: stdout)")
    tel_export.set_defaults(func=_cmd_budget_telemetry_export)


def _cmd_budget_telemetry_records(args) -> None:
    """Show aggregated budget telemetry summary."""
    try:
        from captn.runtime.hermes_profiler import get_all_records, budget_summary
    except ImportError:
        print("ERROR: hermes_profiler module not available")
        return
    summary = budget_summary()
    if summary["enabled_requests"] == 0:
        print("No budget-enabled telemetry records found.")
        print("Context Budget is OFF by default.")
        print("Enable with: export CAPTN_CONTEXT_BUDGET=on")
        return

    print(f"\n  BUDGET TELEMETRY SUMMARY")
    print(f"  {'─'*52}")
    print(f"  {'Enabled requests':30s} {summary['enabled_requests']:>8d}")
    print(f"  {'Total tokens before':30s} {summary['total_before']:>8,d}")
    print(f"  {'Total tokens after':30s} {summary['total_after']:>8,d}")
    print(f"  {'Total reduction':30s} {summary['total_reduction_tokens']:>+8,d} ({summary['total_reduction_pct']:+.1f}%)")
    print(f"  {'Avg reduction per request':30s} {summary['avg_reduction_pct']:>8.1f}%")
    print(f"  {'P50 reduction':30s} {summary['p50_reduction_pct']:>8.1f}%")
    print(f"  {'Total items dropped':30s} {summary['total_items_dropped']:>8d}")
    print(f"  {'System budget exceeded':30s} {summary['system_budget_exceeded']:>8d}")

    if summary.get("warnings"):
        print(f"\n  Warnings:")
        for w in summary["warnings"][:10]:
            print(f"    ⚠  {w}")
    print()


def _cmd_budget_telemetry_record(args) -> None:
    """Show details for a specific request."""
    try:
        from captn.runtime.hermes_profiler import get_all_records
    except ImportError:
        print("ERROR: hermes_profiler module not available")
        return
    records = get_all_records()
    matches = [r for r in records if r.request_id == args.request_id]
    if not matches:
        print(f"No record found for request_id={args.request_id}")
        return
    r = matches[0]
    print(f"\n  REQUEST: {r.request_id}")
    print(f"  {'─'*52}")
    print(f"  {'Timestamp':25s} {r.timestamp}")
    print(f"  {'Model':25s} {r.model or 'N/A'}")
    print(f"  {'Provider':25s} {r.provider or 'N/A'}")
    print()
    print(f"  {'Estimated total':25s} {r.estimated_total:>8,d}")
    print(f"  {'Messages':25s} {r.message_count:>8d} ({r.estimated_messages:>8,d} tok)")
    print(f"  {'Tools':25s} {r.tool_count:>8d} ({r.estimated_tools:>8,d} tok)")
    print()
    print(f"  {'System':25s} {r.system_tokens:>8,d}")
    print(f"  {'History':25s} {r.history_tokens:>8,d}")
    print(f"  {'Memory':25s} {r.memory_tokens:>8,d}")
    print(f"  {'Memory prefetch':25s} {r.memory_prefetch_tokens:>8,d}")
    print(f"  {'Skills':25s} {r.skills_tokens:>8,d}")
    print(f"  {'MCP':25s} {r.mcp_tokens:>8,d}")
    print(f"  {'Tool schemas':25s} {r.tool_schema_tokens:>8,d}")
    print(f"  {'User message':25s} {r.current_user_tokens:>8,d}")
    print(f"  {'Other':25s} {r.other_tokens:>8,d}")
    if r.actual_input_tokens is not None:
        print()
        print(f"  {'Actual input tokens':25s} {r.actual_input_tokens:>8,d}")
        print(f"  {'Actual output tokens':25s} {r.actual_output_tokens or 0:>8,d}")
        print(f"  {'Cache read':25s} {r.cache_read_tokens or 0:>8,d}")
        print(f"  {'Cache write':25s} {r.cache_write_tokens or 0:>8,d}")
    if r.budget_enabled:
        print()
        print(f"  BUDGET:")
        print(f"  {'Before':25s} {r.budget_before_tokens:>8,d}")
        print(f"  {'After':25s} {r.budget_after_tokens:>8,d}")
        print(f"  {'Drop count':25s} {r.budget_drop_count:>8d}")
        print(f"  {'System exceeded':25s} {r.system_budget_exceeded}")
        if r.budget_warnings:
            for w in r.budget_warnings:
                print(f"  ⚠  {w}")
    print()


def _cmd_budget_telemetry_clear(args) -> None:
    """Clear all telemetry records."""
    try:
        from captn.runtime.hermes_profiler import clear_records as _clear
    except ImportError:
        print("ERROR: hermes_profiler module not available")
        return
    n = _clear()
    print(f"Cleared {n} telemetry records.")


def _cmd_budget_telemetry_export(args) -> None:
    """Export telemetry records as JSONL."""
    try:
        from captn.runtime.hermes_profiler import get_all_records
    except ImportError:
        print("ERROR: hermes_profiler module not available")
        return
    records = get_all_records()
    lines = [r.to_jsonl() for r in records]
    output = "\n".join(lines)
    if args.output:
        import os
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output + "\n")
        print(f"Exported {len(lines)} records to {args.output}")
    else:
        print(output)


def _cmd_budget_demo(args) -> None:
    """Show budget selection on a demo payload."""
    # Build a demo payload
    messages = []
    messages.append({"role": "system", "content": "You are a helpful AI assistant. " * 100})
    for i in range(20):
        msg = f"user turn {i} asking about Python and algorithms"
        messages.append({"role": "user", "content": msg * 3})
        messages.append({"role": "assistant", "content": f"response to turn {i} with code examples" * 5})
    messages.append({"role": "user", "content": "What is the GCD of 48 and 180?"})

    memory = "User prefers Python.\nUser works on CaptN-BRAIN.\n" + "Previous session discussed math tools.\n" * 50

    tools = []
    for i in range(20):
        tools.append({"type": "function", "function": {"name": f"tool_{i}", "description": "A tool", "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}})

    retrieved = "Fragment 1: gcd algorithm\nFragment 2: Euclidean algorithm\n" + "Fragment 3: number theory\n" * 30

    budget = ContextBudget(model="deepseek/deepseek-v4-flash")
    result = budget.select(
        system_prompt="You are a helpful AI assistant. " * 100,
        messages=messages,
        memory_text=memory,
        tools=tools,
        retrieved_context=retrieved,
        user_query="What is the GCD of 48 and 180?",
        domain_hint="math",
        dry_run=True,
    )

    print(result.report())

    # Also show before/after summary
    print("\n  BEFORE vs AFTER:")
    print(f"  {'Category':25s} {'Before':>8s} {'After':>8s} {'Budget':>8s}")
    print(f"  {'─'*25:>25s} {'─'*8:>8s} {'─'*8:>8s} {'─'*8:>8s}")
    cats = [
        ("System", result.original_system_tokens, result.selected_system_tokens, result.budget_system),
        ("History", result.original_history_tokens, result.selected_history_tokens, result.budget_history),
        ("Memory", result.original_memory_tokens, result.selected_memory_tokens, result.budget_memory),
        ("Tools", result.original_tool_tokens, result.selected_tool_tokens, result.budget_tools),
        ("Retrieved", result.original_retrieved_tokens, result.selected_retrieved_tokens, result.budget_retrieved),
    ]
    for label, before, after, budget_v in cats:
        pct = f"({(1 - after/max(before, 1))*100:.0f}%)" if before > 0 else ""
        print(f"  {label:25s} {before:>8,d} {after:>8,d} {budget_v:>8,d}  {pct}")
    print(f"  {'─'*25:>25s} {'─'*8:>8s} {'─'*8:>8s}")
    print(f"  {'Total':25s} {result.original_total_tokens:>8,d} {result.selected_total_tokens:>8,d} {result.budget_total:>8,d}  "
          f"({(1 - result.selected_total_tokens/max(result.original_total_tokens, 1))*100:.0f}%)")


def _cmd_budget_status(args) -> None:
    """Show budget configuration."""
    enabled = is_budget_enabled()
    config = BudgetConfig.from_env()
    print(f"Context Budget: {'ENABLED' if enabled else 'DISABLED'}")
    print(f"  CAPTN_CONTEXT_BUDGET={os.environ.get(ENV_BUDGET, 'off')}")
    print()
    print(f"  Total:        {config.total:>6,d} tokens")
    print(f"  System:       {config.system:>6,d} tokens")
    print(f"  History:      {config.history:>6,d} tokens")
    print(f"  Memory:       {config.memory:>6,d} tokens")
    print(f"  Tools:        {config.tools:>6,d} tokens")
    print(f"  Retrieved:    {config.retrieved:>6,d} tokens")
    print()
    print(f"  Enable with:  export CAPTN_CONTEXT_BUDGET=on")
    print(f"  Configure:    export CAPTN_CONTEXT_BUDGET_TOTAL=30000")
    print(f"  Dry-run:      python summon_agents.py devtools budget demo --dry-run")


__all__ = [
    "ContextBudget", "BudgetConfig", "SelectionResult", "SelectionDecision",
    "estimate_provider_tokens", "estimate_messages_tokens", "estimate_tool_tokens",
    "is_budget_enabled", "DEFAULT_BUDGET_CONFIG",
]