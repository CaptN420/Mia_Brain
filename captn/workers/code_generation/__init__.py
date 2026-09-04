"""Code generation workers — produce derived or generated code.

20 workers project-wide, 5 in this specialisation:

Workers
--------
autogen              : Autogenerate code from a crawled dataset using Ollama
                       (qwen2:1.5b). LLM-driven fallback in the deterministic-
                       first pipeline.
deterministic_coder  : Pure-AST Python code generation. Adds docstrings,
                       type-hint scaffolds, and normalises formatting — no LLM.
polyglot_coder       : Multi-language deterministic code generation. Scaffolds
                       and wraps code in C, C++, C#, HTML, JS/TS, Java, Go,
                       Rust, SQL, Shell, CSS — templated, no LLM.
fix_generator        : Generates fix.py scaffold scripts from PGM/error input,
                       validated through ``PatchValidator``.
pgm_worker           : Rule-based PGM (Problem-Guidance-Message) system.
                       Analyses error messages and provides fix suggestions.
code_backend_worker  : Deterministic code quality analysis: cyclomatic
                       complexity, PEP-8 style hints, security scanning, and
                       simple AST refactoring (unused imports). No LLM.
"""