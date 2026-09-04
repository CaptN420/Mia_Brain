"""Captn workers — organised by specialisation (20 workers).

Data Ingestion   captn.workers.data_ingestion (4)
    Pull data from external sources (GitHub) or convert raw local files.

Code Generation  captn.workers.code_generation (6)
    Produce derived/generated code (LLM-based, AST-based, multi-language)
    and analyse code quality (complexity, style, security).

Transformation   captn.workers.transformation (5)
    Mutate, normalise, validate, or translate data — including
    mathematical and chemical formula validation.

Quality          captn.workers.quality (3)
    Validate output, enforce diversity, and detect repetition.

Orchestration    captn.workers.orchestration (2)
    Route tasks to best-suited thinkers with optional opposition mode
    for deliberately diverse, well-rounded answers.
"""