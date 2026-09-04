# Deterministic Processing System

This document explains the **core differentiator** of CaptN-BRAIN: the deterministic processing layer that handles the majority of operations without calling any LLM.

---

## What "Deterministic" Means

In CaptN-BRAIN, a **deterministic operation** is one where:

- **Same input → same output** — reproducible byte-for-byte
- **No LLM involved** — zero token consumption, zero API calls
- **Pure computation** — algorithmic, rule-based, or table-driven
- **Predictable** — the result is a mathematical function of the input, not a model's "opinion"

This is in contrast to LLM-based approaches where the same prompt can produce different outputs each time.

---

## What Can Be Processed Without an LLM

The system handles the following categories deterministically:

### 1. Worker Routing (SmartRouter)

The `SmartRouter` in `captn/workers/orchestration/smart_router.py` maps natural-language task descriptions to the best worker(s) using keyword matching and scoring:

```python
WORKER_ROUTES = {
    "python": ["deterministic_coder", "autogen", "code_backend_worker"],
    "crawl":   ["crawler", "raw2json", "raw2json_worker", "extractor"],
    "math":    ["math_validation_worker", "diversifier_worker", "validator"],
    # ... 110+ entries
}
```

The scoring algorithm considers:
- Exact keyword matches (5.0 points per match)
- Stem matches (3.0 points for 4-char prefix)
- Tag overlap with worker capabilities (1.5 per tag)
- Semantic overlap via description word intersection (0.8 per word)
- **Fairness boost** — underutilized workers get extra points

No LLM is consulted during routing.

### 2. Code Transformation (DeterministicCoder)

The `DeterministicCoder` in `captn/workers/code_generation/deterministic_coder.py` transforms Python source code using pure AST operations:

```python
# Adding missing docstrings — pure AST insertion
def _pass_docstrings(self, tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not ast.get_docstring(node):
                doc = ast.Expr(ast.Constant(value=f"{node.name.capitalize()} ({kind})."))
                node.body.insert(0, doc)
                changed = True
    return changed
```

**Safety:** Before writing, the `behaviorally_equivalent()` function compares the source and output ASTs (minus docstrings/annotations) to guarantee the transform never changes program behavior.

### 3. Patch Validation (PatchValidator)

The `PatchValidator` in `captn/runtime/validator.py` checks proposed code patches before they are written:

- **Critical file protection** — rejects writes to `launcher.py`, `runtime.py`, `main.py`, `base.py` (anchored basename matching, not substring)
- **AST-based dangerous code scan** — detects `subprocess`, `ctypes`, `os.remove`, `shutil.rmtree`, `exec()`, `eval()`, etc.
- Falls back to keyword regex for unparseable patches

### 4. Knowledge Retrieval (CorpusThinkers)

All 20 thinkers parse numbered corpus entries with pure regex:

```python
_ENTRY_RE = re.compile(
    r"^\s*(\d+)\.\s*(?P<name>[A-Z][^:\-]+?)\s*[-–:]\s*(?P<detail>.+?)\s*$"
)
```

No LLM is used for information extraction, just pattern matching. Example entry parsed:

```
1. Dmitri Mendeleev - Created the Periodic Table of Elements in 1869, ...
```

Becomes structured finding: `{"source": "Dmitri Mendeleev", "detail": "Created the Periodic Table..."}`

### 5. Equation Tools (eqsolve, chemsym, math_validator)

Symbolic computation tools:

- `eqsolve` — sympy wrapper for derivative, integral, equation solving, limits, series expansion
- `chemsym` — chemical formula parsing, molar mass calculation, reaction balancing
- `math_validator` — deterministic mathematical expression validation

### 6. Codebase Analysis Tools (depgraph, deadscout, codebase-map, testgen, healthcheck)

| Tool | Deterministic Operation |
|------|------------------------|
| `depgraph` | Parse Python imports, build dependency graph, detect strongly-connected components |
| `deadscout` | Static analysis for dead code detection |
| `codebase-map` | Map directory structure and LOC counts |
| `testgen` | Generate pytest stubs from function signatures |
| `healthcheck` | Composite project health scoring (file organization, test coverage, dependencies) |

### 7. Data Ingestion Workers

- `SyntaxWorker` — file existence and integrity checks
- `BugWorker` — static pattern detection (eval/exec, security risks)
- `Normalizer` — data normalization and formatting
- `Translator` — language translation (not LLM-based, uses rule tables)

### 8. Diversity and Anti-Repetition

- `diversifier_worker` — structural mutation of equations (string manipulation, no LLM)
- `no_repetition_worker` — session-wide anti-duplication registry using structural signatures
- `diversity_checker_worker` — check diversity across outputs

---

## When Deterministic Processing Hands Control to an LLM

The system **only** falls back to an LLM when:

1. **A pipeline step fails** — the `FallbackLLM.generate_recovery_proposal()` is called with strict schema validation
2. **The autogen loop** uses Ollama for code generation from crawled datasets (with `llm_as_fallback` mode)
3. **The MIA debate loop** uses Ollama for equation proposal and criticism (when `llm_enabled=True`)

In all cases, the LLM output is:
- Validated against a strict JSON schema before it can re-enter the pipeline
- Truncated (context is capped at 6000 chars)
- Fenced against prompt injection

---

## Why This Matters

### Token Economy

A typical LLM call for code generation consumes 500-2000+ tokens. The `DeterministicCoder` performs equivalent work with **zero tokens**.

The `SmartRouter` replaces a typical LLM routing call (~300 tokens per decision) with pure keyword matching (~0 tokens).

### Predictability

Deterministic operations are:
- **Reproducible** — the same input always produces the same output
- **Auditable** — every step can be traced through explicit rules
- **Testable** — unit tests can cover all code paths exhaustively

### Latency

| Operation | Typical LLM | Deterministic |
|-----------|------------|---------------|
| Route a task | 1-3 seconds | <10ms |
| Transform code | 5-30 seconds | <100ms |
| Validate a patch | 3-10 seconds | <5ms |
| Check diversity | 5-15 seconds | <50ms |

---

## Limitations of Deterministic Processing

Deterministic processing is not a universal solution. It cannot handle:

- **Open-ended reasoning** — questions requiring creative synthesis or novel insights
- **Natural language understanding** — interpreting ambiguous or nuanced requests
- **Unbounded tasks** — problems that cannot be solved by a fixed set of rules
- **Tasks requiring world knowledge** — facts not encoded in the corpus

When the system encounters these cases, it escalates to the LLM fallback (see [LLM Routing](./LLM_ROUTING.md)).