#!/usr/bin/env python3
"""
Phase 3.23 — Dataset Generator: REAL LARGE-CONTEXT scenarios.

Generates 175–200 realistic large-context scenarios with token calibration.
Uses the real estimate_provider_tokens() to hit target buckets.
"""
from __future__ import annotations

import hashlib
import json
import random
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, ".")
sys.path.insert(0, "captn")

# ── Seed for reproducibility ──
RANDOM_SEED = 3232024
random.seed(RANDOM_SEED)

# ── Token estimator (lazy) ──
_ESTIMATOR_MODEL = "deepseek/deepseek-v4-flash"

def _load_estimator():
    """Lazy-load the best token estimator."""
    from captn.runtime.context_budget import estimate_provider_tokens, estimate_messages_tokens, estimate_tool_tokens
    return estimate_provider_tokens, estimate_messages_tokens, estimate_tool_tokens

def _tokens(text: str) -> int:
    est, _, _ = _load_estimator()
    return est(text, _ESTIMATOR_MODEL)

def _msg_tokens(msgs: list) -> int:
    _, est_msgs, _ = _load_estimator()
    return est_msgs(msgs, _ESTIMATOR_MODEL)

def _tool_tokens(tools: list) -> int:
    _, _, est_tools = _load_estimator()
    return est_tools(tools, _ESTIMATOR_MODEL)

# ── Scenario families ──
SCENARIO_FAMILIES = [
    "long_coding_conversation",
    "long_tool_history",
    "mcp_heavy",
    "mixed_context",
    "long_debug_session",
    "long_research_session",
]

TOOL_NAMES = [
    "search_web", "read_file", "write_file", "list_directory",
    "execute_command", "search_files", "git_diff", "git_log",
    "python_execute", "bash_shell", "web_extract", "web_search",
    "mcp_github_search", "mcp_filesystem_read", "mcp_db_query",
    "mcp_slack_send", "mcp_jira_create", "mcp_email_send",
    "delegate_task", "read_terminal", "read_file_lines",
    "create_file", "patch_file", "calculate", "translate",
    "summarize_code", "analyze_dependency", "find_bug",
    "code_review", "generate_test", "refactor_function",
]

LANGUAGES = ["Python", "TypeScript", "Go", "Rust", "Java", "C++"]


def _make_large_tool_schema(name: str, verbose: bool = True) -> Dict[str, Any]:
    """Build a tool schema with substantial description and parameters."""
    desc = f"Execute the {name} operation. This tool provides comprehensive {name} functionality for the agent runtime. It handles input validation, error recovery, and logging. When called, it performs the requested operation and returns structured results with metadata including timing, exit codes, and any error messages. Supports both synchronous and asynchronous execution modes. For complex operations, it can return paginated results."
    params = {
        "type": "object",
        "properties": {
            "input": {"type": "string", "description": f"Primary input for {name}. This is the main data or command to process. Supports text, JSON, and query formats."},
            "options": {
                "type": "object",
                "description": "Additional configuration options for this operation.",
                "properties": {
                    "verbose": {"type": "boolean", "description": "Enable detailed output logging"},
                    "timeout": {"type": "integer", "description": "Operation timeout in seconds (default: 30)"},
                    "format": {"type": "string", "description": "Output format: json, text, or table"},
                    "max_results": {"type": "integer", "description": "Maximum results to return"},
                },
            },
            "context": {
                "type": "object",
                "description": "Contextual information for the operation.",
                "properties": {
                    "session_id": {"type": "string"},
                    "task_id": {"type": "string"},
                    "priority": {"type": "integer", "description": "1=low, 2=normal, 3=high"},
                },
            },
        },
        "required": ["input"],
    }
    if verbose:
        # Extra parameter documentation to inflate schema size
        params["properties"]["filters"] = {
            "type": "object",
            "description": f"Optional filters for {name} results. Each filter is a key-value pair limiting results to matching entries.",
            "additionalProperties": {"type": "string"},
        }
        params["properties"]["pagination"] = {
            "type": "object",
            "description": "Pagination settings for large result sets.",
            "properties": {
                "page": {"type": "integer", "description": "Page number (1-indexed)"},
                "page_size": {"type": "integer", "description": "Items per page (max 100)"},
                "sort_by": {"type": "string", "description": "Field to sort results by"},
                "sort_order": {"type": "string", "enum": ["asc", "desc"]},
            },
        }
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": params}}


def _make_system_prompt(target_tokens: int) -> str:
    """Build a system prompt with realistic content."""
    core = """You are CaptN-BRAIN, a deterministic-first AI engineering agent operating in a multi-agent architecture. You help with software development, system analysis, research, and automation. Key principles:

1. **Deterministic-first**: prefer fast, local, deterministic solutions over LLM-based approaches
2. **Measure before optimizing**: always establish baselines before making changes
3. **Token economy**: minimize unnecessary context in every request
4. **Verify results**: validate all outputs before claiming success
5. **Log decisions**: record significant decisions and their rationale

Your runtime environment:
- Python 3.11 with asyncio support
- Access to file system, web, git, and system tools
- Multi-agent orchestration with thinkers and workers
- Token profiling via CAPTN_TOKEN_PROFILING env var
- Context budget via CAPTN_CONTEXT_BUDGET

Key files:
- summon_agents.py — CLI entry point
- captn/runtime/ — core runtime logic
- captn/cli/ — CLI command modules
- tools/ — standalone tool modules"""
    return core


def _code_block(lang: str, size: str = "medium") -> str:
    """Generate a realistic code block of specified size."""
    if size == "small":
        return f"""```{lang.lower()}
def process(data: Dict[str, Any]) -> Result:
    \"\"\"Process input data with validation.\"\"\"
    if not data:
        return Result.error("empty_input")
    validated = validate(data)
    return validated if validated.success else validate_fallback(data)
```"""
    elif size == "medium":
        return f"""```{lang.lower()}
class RequestHandler:
    \"\"\"Handles incoming API requests with validation, routing, and response formatting.\"\"\"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {{}}
        self.validators: Dict[str, Validator] = {{}}
        self._init_validators()

    def _init_validators(self) -> None:
        \"\"\"Initialize input validators from configuration.\"\"\"
        for name, spec in self.config.get("validators", {{}}).items():
            if spec.get("type") == "schema":
                self.validators[name] = SchemaValidator(spec)
            elif spec.get("type") == "custom":
                self.validators[name] = CustomValidator(spec)

    async def handle(self, request: Request) -> Response:
        \"\"\"Process a single request through the pipeline.\"\"\"
        try:
            validated = await self._validate_request(request)
            if not validated.success:
                return Response(status=400, error=validated.error)
            result = await self._route_request(validated.data)
            return Response(status=200, data=result)
        except ValidationError as e:
            return Response(status=422, error=str(e))
        except Exception as e:
            logger.exception("Unexpected error handling request")
            return Response(status=500, error="internal_error")
```"""
    else:  # large
        return f"""```{lang.lower()}
import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

@dataclass
class CacheEntry:
    \"\"\"A single cached result with metadata.\"\"\"
    key: str
    value: Any
    timestamp: float
    ttl: int
    access_count: int = 0
    size_bytes: int = 0

class LRUCache:
    \"\"\"Thread-safe LRU cache with TTL support and size limits.\"\"\"

    def __init__(self, max_size: int = 1024, default_ttl: int = 300):
        self.max_size = max_size
        self.default_ttl = default_ttl
        self._cache: Dict[str, CacheEntry] = {{}}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[Any]:
        \"\"\"Retrieve a cached value. Returns None if not found or expired.\"\"\"
        async with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if time.time() - entry.timestamp > entry.ttl:
                del self._cache[key]
                return None
            entry.access_count += 1
            # Move to end (most recently used)
            val = entry.value
            del self._cache[key]
            self._cache[key = entry
            return val

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        \"\"\"Store a value in the cache with optional TTL.\"\"\"
        async with self._lock:
            if len(self._cache) >= self.max_size:
                # Evict least recently used
                lru_key = min(self._cache.keys(),
                              key=lambda k: self._cache[k].access_count)
                del self._cache[lru_key]
            self._cache[key] = CacheEntry(
                key=key, value=value,
                timestamp=time.time(),
                ttl=ttl or self.default_ttl,
            )

    async def clear(self) -> int:
        \"\"\"Clear all cache entries. Returns count of cleared entries.\"\"\"
        async with self._lock:
            count = len(self._cache)
            self._cache.clear()
            return count

    @property
    def size(self) -> int:
        return len(self._cache)
```"""


def _gen_scenario(
    scenario_id: str,
    family: str,
    target_tokens: int,
) -> Dict[str, Any]:
    """Generate a scenario, then calibrate to hit target_tokens."""
    if family == "long_coding_conversation":
        return _gen_coding(scenario_id, family, target_tokens)
    elif family == "long_tool_history":
        return _gen_tool_history(scenario_id, family, target_tokens)
    elif family == "mcp_heavy":
        return _gen_mcp(scenario_id, family, target_tokens)
    elif family == "mixed_context":
        return _gen_mixed(scenario_id, family, target_tokens)
    elif family == "long_debug_session":
        return _gen_debug(scenario_id, family, target_tokens)
    elif family == "long_research_session":
        return _gen_research(scenario_id, family, target_tokens)
    else:
        return _gen_coding(scenario_id, family, target_tokens)


def _expand(old: Dict[str, Any], min_add: int = 3000, max_add: int = 8000, aggressive: bool = False) -> Dict[str, Any]:
    """Expand a scenario by adding more turns and larger tool schemas."""
    s = dict(old)
    msgs = list(s["messages"])
    tools = list(s["tools"])

    # Add more assistant responses with large code blocks
    extra_turns = random.randint(3, 8) if aggressive else random.randint(2, 5)
    for _ in range(extra_turns):
        lang = random.choice(LANGUAGES)
        sizes = ["large"] * 3 if aggressive else ["medium", "large", "large"]
        size = random.choice(sizes)
        code = _code_block(lang, size)
        # Longer user messages for aggressive mode
        if aggressive:
            user = f"Let's continue with more detail. Can you show me the complete implementation for {random.choice(['error handling', 'input validation', 'async processing', 'data transformation', 'pagination', 'thread safety', 'memory management', 'logging infrastructure', 'monitoring', 'backpressure handling', 'rate limiting', 'circuit breaker'])}? Include all edge cases, tests, and documentation."
        else:
            user = f"Let's continue. Can you show me the implementation for {random.choice(['error handling', 'input validation', 'async processing', 'data transformation', 'pagination'])}?"
        msgs.append({"role": "user", "content": user})
        asst = f"Here's the implementation:\n\n{code}\n\nThis approach handles edge cases, follows best practices, and is fully documented. Let me know if you need modifications.\n\nAdditional notes:\n- Time complexity: {random.choice(['O(n)', 'O(n log n)', 'O(1)', 'O(n²)'])}\n- Space complexity: {random.choice(['O(n)', 'O(1)', 'O(log n)'])}\n- Thread-safe: {random.choice(['Yes', 'Yes, with lock', 'No'])}\n- Error handling: {random.choice(['Comprehensive', 'Basic', 'With retries'])}"
        msgs.append({"role": "assistant", "content": asst})

        if random.random() < 0.4:
            tool_content = f"Tool verification: Implementation validated successfully. No issues found. {random.randint(100, 9999)} lines of code analyzed. Performance: {random.choice(['optimal', 'acceptable', 'needs optimization'])}."
            msgs.append({"role": "tool", "content": tool_content, "tool_call_id": f"ext_call_{random.randint(0, 999)}"})

    # Add more tools with larger schemas
    extra_tools = random.randint(8, 18) if aggressive else random.randint(5, 12)
    for _ in range(extra_tools):
        name = random.choice(TOOL_NAMES)
        if name not in [t.get("function", {}).get("name", "") for t in tools]:
            tools.append(_make_large_tool_schema(name))

    s["messages"] = msgs
    s["tools"] = tools
    return s


def _compute_tokens(s: Dict[str, Any]) -> int:
    """Compute total estimated tokens for a scenario using real tokenizer."""
    return (
        _tokens(s.get("system_prompt", ""))
        + _msg_tokens(s.get("messages", []))
        + _tool_tokens(s.get("tools", []))
        + _tokens(s.get("memory_text", ""))
        + _tokens(s.get("retrieved_context", ""))
    )


def _gen_coding(scenario_id: str, family: str, target: int) -> Dict[str, Any]:
    lang = random.choice(LANGUAGES)
    topic = random.choice([
        f"implement a {lang} REST API with authentication",
        f"refactor legacy {lang} code for async patterns",
        f"add comprehensive error handling to {lang} services",
        f"optimize {lang} database queries with connection pooling",
        f"write unit tests for {lang} microservices with mocking",
        f"debug {lang} memory leak in production systems",
        f"migrate {lang} monolith to event-driven architecture",
    ])
    msgs = []
    n_turns = random.randint(6, 12)
    for i in range(n_turns):
        code_size = "small" if i % 3 == 0 else ("medium" if i % 3 == 1 else "large")
        code = _code_block(lang, code_size)
        user = f"Step {i+1}: Let's work on {topic}. I need you to {random.choice(['implement', 'review', 'test', 'refactor', 'optimize', 'document'])} this component." if i < n_turns - 1 else f"Final review: We've completed {n_turns} steps. Can you do a comprehensive review of all changes, verify edge cases, and confirm the implementation is production-ready?"
        asst = f"I've completed the work for step {i+1}.\n\n{code}\n\nKey considerations:\n- All edge cases handled\n- Proper error types used\n- Logging added for production debugging\n- Performance optimized for high throughput" if i < n_turns - 1 else f"COMPREHENSIVE REVIEW COMPLETE.\n\nAll {n_turns} steps verified successfully. Summary:\n- Implementation complete: all requirements satisfied\n- Edge cases handled: empty input, null values, timeout, overflow\n- Error handling: comprehensive with typed errors\n- Performance: optimized within expected parameters\n- Tests: coverage adequate for production deployment"
        msgs.append({"role": "user", "content": user})
        msgs.append({"role": "assistant", "content": asst})
        if random.random() < 0.3:
            tc = f"Tool result {i}: Analysis found {random.randint(0, 3)} issues. All minor. Verification passed."
            msgs.append({"role": "tool", "content": tc, "tool_call_id": f"c_{i}"})

    msgs.append({"role": "user", "content": "What is the final status? Are we ready to merge? Any remaining blockers?"})

    tools = [_make_large_tool_schema(n) for n in random.sample(TOOL_NAMES, min(25, len(TOOL_NAMES)))]
    return {"id": scenario_id, "family": family, "model": _ESTIMATOR_MODEL,
         "system_prompt": _make_system_prompt(target // 5), "messages": msgs,
         "tools": tools, "memory_text": "", "retrieved_context": "", "domain_hint": "software"}


def _gen_tool_history(scenario_id: str, family: str, target: int) -> Dict[str, Any]:
    msgs = []
    n_calls = random.randint(10, 20)
    for i in range(n_calls):
        tname = random.choice(TOOL_NAMES)
        msgs.append({"role": "assistant", "content": f"Using {tname} for task {i}..."})
        msgs.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"tcall_{i}", "type": "function", "function": {"name": tname, "arguments": json.dumps({"input": f"task_{i}", "options": {"verbose": True, "timeout": 30}})}}
        ]})
        result = f"Result from {tname}: Operation completed. " + "\n".join([
            f"- File {f}: {random.randint(100, 50000)} bytes"
            for f in [f"src/{random.choice(['main', 'utils', 'api', 'models'])}.{random.choice(['py', 'ts', 'go', 'rs'])}" for _ in range(random.randint(3, 8))]
        ])
        msgs.append({"role": "tool", "content": result, "tool_call_id": f"tcall_{i}"})

    msgs.append({"role": "user", "content": f"Analyze all {n_calls} tool calls. What patterns emerge? Which tools are most used? Any optimization opportunities?"})

    tools = [_make_large_tool_schema(n) for n in random.sample(TOOL_NAMES, min(30, len(TOOL_NAMES)))]
    return {"id": scenario_id, "family": family, "model": _ESTIMATOR_MODEL,
         "system_prompt": _make_system_prompt(target // 4), "messages": msgs,
         "tools": tools, "memory_text": "", "retrieved_context": "", "domain_hint": "software"}


def _gen_mcp(scenario_id: str, family: str, target: int) -> Dict[str, Any]:
    mcp_names = [
        "mcp_github_search", "mcp_filesystem_read", "mcp_db_query",
        "mcp_slack_send", "mcp_jira_create", "mcp_email_send",
        "mcp_notion_query", "mcp_linear_create", "mcp_drive_read",
        "mcp_docs_create", "mcp_calendar_read", "mcp_deploy_trigger",
        "mcp_monitor_query", "mcp_log_search", "mcp_analytics_query",
    ]
    msgs = [{"role": "user", "content": "Let me check project status across all services using MCP tools."}]
    for i in range(random.randint(6, 14)):
        name = random.choice(mcp_names)
        msgs.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"mcp_{i}", "type": "function", "function": {"name": name, "arguments": "{}"}}
        ]})
        msgs.append({"role": "tool", "content": f"MCP {name}: {random.choice(['Status OK', 'Data retrieved', 'Action created', 'Query complete', 'Logs analyzed'])}. Returned {random.randint(10, 500)} items.", "tool_call_id": f"mcp_{i}"})
    msgs.append({"role": "user", "content": "Consolidate all MCP results. What's the overall project status?"})

    tools = [_make_large_tool_schema(n) for n in mcp_names + random.sample(TOOL_NAMES, min(15, len(TOOL_NAMES)))]
    return {"id": scenario_id, "family": family, "model": _ESTIMATOR_MODEL,
         "system_prompt": _make_system_prompt(target // 4), "messages": msgs,
         "tools": tools, "memory_text": "", "retrieved_context": "", "domain_hint": "software"}


def _gen_debug(scenario_id: str, family: str, target: int) -> Dict[str, Any]:
    lang = random.choice(LANGUAGES)
    bug = random.choice(["memory leak", "race condition", "null pointer", "API compatibility", "performance regression", "type mismatch", "concurrent modification", "resource exhaustion"])
    msgs = []
    n_steps = random.randint(8, 14)
    for i in range(n_steps):
        code = _code_block(lang, random.choice(["medium", "large"]))
        user = f"Debug step {i+1}: I'm investigating a {bug} in production. Here's what I found so far:\n```\nException: {bug.upper()} at {random.choice(['handler.py:88', 'service.ts:201', 'main.go:67', 'worker.rs:44'])}\n```" if i < n_steps - 1 else f"After {n_steps} debugging steps, what is your FINAL root cause analysis? What fix do you recommend?"
        asst = f"Analysis for step {i+1}:\n{code}\nThe {bug} appears related to {random.choice(['missing synchronization', 'buffer overflow', 'resource cleanup', 'incorrect ordering', 'type coercion', 'exception handling'])}.\nRemaining uncertainty: {random.choice(['none', 'needs reproduction', 'needs load test', 'needs log analysis'])}."
        msgs.append({"role": "user", "content": user})
        msgs.append({"role": "assistant", "content": asst})
        if random.random() < 0.4:
            msgs.append({"role": "tool", "content": f"Debug tool: Memory={random.randint(100, 2000)}MB, CPU={random.randint(20, 99)}%, Suspected leak in loop iteration {random.randint(100, 9999)}.", "tool_call_id": f"dbg_{i}"})
    tools = [_make_large_tool_schema(n) for n in random.sample(TOOL_NAMES, min(25, len(TOOL_NAMES)))]
    return {"id": scenario_id, "family": family, "model": _ESTIMATOR_MODEL,
         "system_prompt": _make_system_prompt(target // 4), "messages": msgs,
         "tools": tools, "memory_text": "", "retrieved_context": "", "domain_hint": "software"}


def _gen_research(scenario_id: str, family: str, target: int) -> Dict[str, Any]:
    topic = random.choice(["transformer architecture", "reinforcement learning", "graph neural networks", "attention mechanisms", "diffusion models", "causal inference", "Bayesian optimization", "quantum error correction"])
    msgs = []
    n_steps = random.randint(4, 8)
    for i in range(n_steps):
        paper = f"A Novel {' '.join(random.sample(['Scalable', 'Efficient', 'Robust', 'Adaptive', 'Probabilistic', 'Hierarchical'], 3))} Approach to {topic}"
        msgs.append({"role": "user", "content": f"Find papers on {topic}, specifically about {paper}."})
        asst = f"Found relevant papers on {topic}:\n1. {paper} — achieves {random.randint(85, 99)}% on {random.choice(['standard benchmarks', 'real-world dataset', 'adversarial tests'])}\n2. Related work by {random.choice(['Smith et al.', 'Johnson et al.', 'Wang et al.', 'Chen et al.'])} (2023-2024) — proposes {random.choice(['attention-based', 'convolutional', 'recurrent', 'graph-based'])} approach\n3. Key insight: combining {random.choice(['sparsity', 'locality', 'hierarchy', 'symmetry'])} with {topic} yields significant improvements\nDetailed analysis follows below.\n\n**Methodology:**\nThe proposed approach uses a novel {random.choice(['attention mechanism', 'loss function', 'training procedure', 'architecture design'])} that addresses the key limitations of existing methods. Specifically, it handles {random.choice(['long-range dependencies', 'high-dimensional data', 'sparse gradients', 'non-stationary distributions'])} more effectively.\n\n**Results:**\n| Metric | Baseline | Proposed | Improvement |\n|--------|----------|----------|-------------|\n| Accuracy | {random.randint(75, 85)}% | {random.randint(88, 98)}% | +{random.randint(5, 20)}% |\n| Speed | {random.randint(100, 500)}ms | {random.randint(30, 200)}ms | {random.randint(2, 10)}x |\n| Memory | {random.randint(1, 8)}GB | {random.randint(1, 4)}GB | -{random.randint(20, 60)}% |"
        msgs.append({"role": "assistant", "content": asst})

    retrieved = "\n\n## Retrieved Papers\n"
    for j in range(random.randint(5, 10)):
        retrieved += f"### Paper {j+1}: {' '.join(random.sample(['Deep', 'Neural', 'Adaptive', 'Scalable', 'Probabilistic', 'Hierarchical'], 3))} {topic}\n"
        retrieved += f"*Authors: {random.choice(['Liu et al.', 'Wang et al.', 'Zhang et al.', 'Garcia et al.'])}*\n"
        retrieved += f"*Abstract:* We present a framework for {topic} that achieves SOTA on multiple benchmarks. Key innovation: {random.choice(['hierarchical attention', 'sparse gradient estimator', 'adaptive loss', 'multi-scale fusion'])}.\n\n"

    msgs.append({"role": "user", "content": f"Synthesize all findings on {topic}. What are the open challenges? What approach do you recommend for a new project?"})

    tools = [_make_large_tool_schema(n) for n in random.sample(TOOL_NAMES, min(20, len(TOOL_NAMES)))]
    retrieved_text = retrieved
    return {"id": scenario_id, "family": family, "model": _ESTIMATOR_MODEL,
         "system_prompt": _make_system_prompt(target // 5), "messages": msgs,
         "tools": tools, "memory_text": "", "retrieved_context": retrieved_text, "domain_hint": "software"}


def _gen_mixed(scenario_id: str, family: str, target: int) -> Dict[str, Any]:
    msgs = []
    n_turns = random.randint(8, 14)
    for i in range(n_turns):
        ttype = random.choice(["coding", "research", "debugging", "planning", "review"])
        if ttype == "coding":
            code = _code_block(random.choice(LANGUAGES), random.choice(["medium", "large"]))
            msgs.append({"role": "user", "content": f"Implement a function that {random.choice(['validates input', 'processes batch data', 'handles concurrent requests', 'manages resource pools'])}."})
            msgs.append({"role": "assistant", "content": f"Implementation:\n{code}\nTest coverage: {random.randint(80, 100)}%. All edge cases handled."})
        elif ttype == "research":
            msgs.append({"role": "user", "content": f"What are the latest advances in {random.choice(['distributed ML', 'privacy preservation', 'edge computing', 'model optimization'])}?"})
            msgs.append({"role": "assistant", "content": f"Recent advances show {random.randint(10, 50)}% improvement in {random.choice(['throughput', 'latency', 'accuracy', 'memory efficiency'])} compared to prior work. Key papers include..."})
        else:
            msgs.append({"role": "user", "content": f"Let's {ttype} the current {random.choice(['architecture', 'implementation', 'pipeline', 'deployment'])}."})
            asst = f"{ttype.title()} in progress. {random.randint(5, 50)} items to review. "
            asst += f"Found {random.randint(0, 10)} issues, {random.randint(0, 3)} critical. Recommendation: {random.choice(['proceed', 'blocked until fix', 'needs more data', 'approved conditionally'])}."
            msgs.append({"role": "assistant", "content": asst})

    memory = "## User Profile & History\n"
    memory += "- Name: Developer\n- Preferred lang: Python 3.11+\n- Project: CaptN-BRAIN\n- Style: deterministic-first, minimal LLM\n"
    for j in range(random.randint(8, 15)):
        memory += f"- Session {j}: Worked on {random.choice(['context budget', 'tool necessity', 'history pruning', 'retrieval compression', 'quality validation', 'token profiling', 'adaptive routing', 'fragment registry'])}\n"

    retrieved = "\n## Documents\n"
    for j in range(random.randint(5, 10)):
        retrieved += f"### {random.choice(['API Spec', 'Architecture Guide', 'Contrib Docs', 'Release Notes', 'Technical RFC', 'Migration Guide'])} {j+1}\n"
        retrieved += f"{random.choice(['The system architecture consists of...', 'API endpoints provide...', 'To contribute follow...', 'Version N includes...', 'Migration steps:...'])}\n\n"

    msgs.append({"role": "user", "content": "Given our history, memory, and documents, what are the top 3 improvements for this project?"})

    tools = [_make_large_tool_schema(n) for n in random.sample(TOOL_NAMES, min(30, len(TOOL_NAMES)))]
    return {"id": scenario_id, "family": family, "model": _ESTIMATOR_MODEL,
         "system_prompt": _make_system_prompt(target // 4), "messages": msgs,
         "tools": tools, "memory_text": memory, "retrieved_context": retrieved, "domain_hint": "software"}


def generate_dataset() -> List[Dict[str, Any]]:
    """Generate 175 large-context scenarios across token buckets."""
    buckets = {
        "10K": (10_000, 20_000, 50),
        "20K": (20_000, 30_000, 50),
        "30K": (30_000, 50_000, 50),
        "50K": (50_000, 80_000, 25),
    }
    scenarios = []
    families = list(SCENARIO_FAMILIES)
    idx = 0

    for bucket, (lo, hi, count) in buckets.items():
        for i in range(count):
            family = families[idx % len(families)]
            target = random.randint(lo, hi)

            # More iterations needed for 50K bucket
            aggressive = bucket == "50K"

            sid = f"req_323_{bucket}_{i:03d}"
            s = _gen_scenario(sid, family, target)

            # Calibration loop — up to 16 iterations for 50K
            max_iters = 16 if aggressive else 8
            for _ in range(max_iters):
                act = _compute_tokens(s)
                if act >= target:
                    break
                s = _expand(s, aggressive=aggressive)

            scenarios.append(s)
            idx += 1

    # Sort by bucket order
    bucket_order = {"10K": 0, "20K": 1, "30K": 2, "50K": 3}
    scenarios.sort(key=lambda s: (bucket_order.get(s["id"].split("_")[2], 99), s["id"]))
    return scenarios


def compute_buckets(scenarios: List[Dict[str, Any]]) -> Dict[str, list]:
    """Compute actual token buckets."""
    buckets: Dict[str, list] = {"<10K": [], "10-20K": [], "20-30K": [], "30-50K": [], ">50K": []}
    for s in scenarios:
        t = _compute_tokens(s)
        if t < 10000:
            buckets["<10K"].append(t)
        elif t < 20000:
            buckets["10-20K"].append(t)
        elif t < 30000:
            buckets["20-30K"].append(t)
        elif t < 50000:
            buckets["30-50K"].append(t)
        else:
            buckets[">50K"].append(t)
    return buckets


def save_dataset(scenarios: List[Dict[str, Any]], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for s in scenarios:
            f.write(json.dumps(s, ensure_ascii=False, default=str) + "\n")


def load_dataset(path: str) -> List[Dict[str, Any]]:
    scenarios = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                scenarios.append(json.loads(line))
    return scenarios


if __name__ == "__main__":
    print("Generating Phase 3.23 large-context dataset...")
    sys.stdout.flush()
    dataset = generate_dataset()
    save_dataset(dataset, "/tmp/phase323_dataset.jsonl")
    print(f"Dataset: {len(dataset)} scenarios saved to /tmp/phase323_dataset.jsonl")
    sys.stdout.flush()

    # Bucket summary
    buckets = compute_buckets(dataset)
    for bucket, tokens in buckets.items():
        if tokens:
            mean = sum(tokens) // len(tokens)
            med = sorted(tokens)[len(tokens)//2]
            print(f"  {bucket}: {len(tokens)} — mean={mean:,} median={med:,}")
        else:
            print(f"  {bucket}: 0")
    sys.stdout.flush()

    # Family breakdown
    from collections import Counter
    families = Counter(s["family"] for s in dataset)
    for fam, cnt in families.most_common():
        print(f"  Family '{fam}': {cnt}")