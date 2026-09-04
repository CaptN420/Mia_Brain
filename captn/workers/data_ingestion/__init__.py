"""Data ingestion workers — pull data from external sources or convert raw files.

Workers
--------
crawler         : GitHub open-source code crawler. Pulls source trees from
                   public repositories and feeds the ``UniversalData`` schema.
raw2json        : Transform raw local files (code, docs, configs) into
                   structured JSONL (``captn.crawler.dataset/1.0`` schema).
raw2json_worker : Captn Plugin wrapper around ``raw2json``, driven by the
                   message bus.
extractor       : Read files from the local filesystem (paths in message
                   payload) and emit ``UniversalData`` records.
"""