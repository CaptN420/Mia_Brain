"""One-time cleanup for legacy autogen artifacts (AUDIT-A).

- Moves any generated/*.gen.py that FAILS the current behavioral-equivalence
  gate into generated/_quarantine/ (never deletes user files).
- Rebuilds Code_base/dataset.autogen.jsonl keeping only records whose content
  matches a file that passes the gate (or whose source cannot be found --
  unverifiable ones are quarantined too).

Idempotent: safe to run multiple times.
"""
import glob
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from captn.workers.autogen import behaviorally_equivalent  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
GEN_DIR = os.path.join(ROOT, "generated")
QUARANTINE = os.path.join(GEN_DIR, "_quarantine")
CORPUS = os.path.join(ROOT, "Code_base", "dataset.autogen.jsonl")


def find_orig(path_suffix):
    for candidate in (
        os.path.join(ROOT, "_converted", "dataset.jsonl"),
        os.path.join(ROOT, "Code_base", "dataset.jsonl"),
    ):
        if not os.path.exists(candidate):
            continue
        for line in open(candidate, encoding="utf-8", errors="replace"):
            try:
                r = json.loads(line)
            except Exception:
                continue
            p = (r.get("path") or "").replace("\\", "/")
            if p.endswith(path_suffix):
                return r.get("content")
    return None


def main():
    os.makedirs(QUARANTINE, exist_ok=True)
    moved, kept = [], []

    for g in sorted(glob.glob(os.path.join(GEN_DIR, "*.gen.py"))):
        stem = os.path.basename(g).replace(".gen.py", ".py")
        orig = find_orig(stem)
        gen = open(g, encoding="utf-8").read()
        if orig is None or not behaviorally_equivalent(orig, gen):
            dest = os.path.join(QUARANTINE, os.path.basename(g))
            shutil.move(g, dest)
            moved.append(os.path.basename(g))
        else:
            kept.append(os.path.basename(g))

    print(f"quarantined {len(moved)}: {moved}")
    print(f"kept        {len(kept)}: {kept}")

    # Rebuild the autogen corpus without quarantined content.
    if os.path.exists(CORPUS):
        good_lines = []
        removed = 0
        kept_hashes = set()
        for g in kept:
            p = os.path.join(GEN_DIR, g)
            kept_hashes.add("sha256:" + __import__("hashlib").sha256(
                open(p, encoding="utf-8").read().encode()).hexdigest())
        for line in open(CORPUS, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            h = "sha256:" + __import__("hashlib").sha256(
                (rec.get("content") or "").encode()).hexdigest()
            if h in kept_hashes:
                good_lines.append(line)
            else:
                removed += 1
        with open(CORPUS, "w", encoding="utf-8") as fh:
            fh.write("\n".join(good_lines) + ("\n" if good_lines else ""))
        print(f"corpus: removed {removed} poisoned record(s), kept {len(good_lines)}")


if __name__ == "__main__":
    main()
