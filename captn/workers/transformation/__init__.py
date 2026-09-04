"""Transformation workers — mutate, normalise, or translate data.

Workers
--------
normalizer                 : Parse raw HTML/JSON/XML and convert into
                             ``UniversalData`` format.
translator                 : Transform / re-format content (regex cleaning,
                             markdown formatting, key-value extraction).
diversifier_worker         : Generate structurally distinct equation variants.
                             Deterministic (no LLM). Applies a fixed menu of
                             structural mutations.
math_validation_worker     : Deterministic mathematical validation: formula
                             parsing, safe evaluation, dimensional analysis,
                             and equation verification. Pure Python + optional
                             sympy. No LLM.
chemistry_validation_worker: Deterministic chemistry validation: formula
                             parsing, reaction balancing, charge & mass
                             conservation. Pure Python regex+counter. No LLM.
"""