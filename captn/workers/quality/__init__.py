"""Quality / safety workers — validate output and enforce diversity.

Workers
--------
validator                : Final validation rules (e.g. presence of required
                           fields like ``title`` and ``body``).
no_repetition_worker     : Enforce structural diversity across the whole run.
                           Tracks every equation by structure signature and
                           symbol-set fingerprint. Rejects repetitions.
diversity_checker_worker : Cross-result diversity enforcement. Detects
                           structural repetition, domain clustering bias, and
                           perspective imbalance. Suggests opposing-domain
                           thinkers for correction.
"""