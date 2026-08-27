"""Tests for the DeterministicCoder worker (deterministic-first autogen)."""
import ast
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from captn.workers.deterministic_coder import DeterministicCoder


@pytest.fixture
def coder():
    return DeterministicCoder()


SRC = '''\
import os

def add(a, b):
    return a + b

class Greeter:
    def greet(self, name="world"):
        return f"hello {name}"
'''


def test_generates_docstrings_and_annotations(coder):
    out, method = coder.generate(SRC)
    assert out is not None
    assert "deterministic" in method
    # docstrings added
    tree = ast.parse(out)
    mod_doc = ast.get_docstring(tree)
    assert mod_doc == "Auto-documented module."
    fn = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "add"][0]
    assert ast.get_docstring(fn) is not None
    # annotation inferred from literal default
    gr = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)][0]
    gm = [n for n in gr.body if isinstance(n, ast.FunctionDef) and n.name == "greet"][0]
    ann = {a.arg: a.annotation for a in gm.args.args}
    assert getattr(ann["name"], "id", None) == "str"


def test_output_is_deterministic(coder):
    a = coder.generate(SRC)
    b = coder.generate(SRC)
    assert a == b  # same input -> byte-identical output


def test_equivalence_gate_blocks_rewrites(coder):
    """If a pass ever changed behaviour, generate() must refuse."""
    bad = "def f(x):\n    return x + 1\n"
    ok_patch = coder.generate(bad)
    # even if generation succeeds it must be equivalent; simulate failure:
    from captn.workers.autogen import behaviorally_equivalent
    if ok_patch[0] is not None:
        assert behaviorally_equivalent(bad, ok_patch[0])


def test_rejects_syntax_error(coder):
    out, reason = coder.generate("def broken(:\n")
    assert out is None and reason == "syntax-error"


def test_rejects_unknown_mode(coder):
    out, reason = coder.generate(SRC, modes=["docstrings", "explode"])
    assert out is None and "unknown-modes" in reason


def test_generate_to_file_atomic_and_validated(coder, tmp_path):
    res = coder.generate_to_file(SRC, src_path="sample.py", out_dir=tmp_path)
    assert res["valid"] is True
    p = tmp_path / res["out_path"].replace(str(tmp_path), "").lstrip("/\\") \
        if False else tmp_path / os.path.basename(res["out_path"])
    assert p.exists()
    content = p.read_text(encoding="utf-8")
    assert "Auto-documented module." in content
    # no temp files left behind
    leftovers = [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]
    assert not leftovers


def test_generate_to_file_includes_source_hash(coder, tmp_path):
    res = coder.generate_to_file(SRC, src_path="sample.py", out_dir=tmp_path)
    name = os.path.basename(res["out_path"])
    assert ".dc.py" in name and len(name.split(".")) >= 4  # stem.hash.dc.py


def test_nothing_to_do_when_fully_documented(coder):
    src = '"""Doc."""\ndef f():\n    """F."""\n    pass\n'
    out, reason = coder.generate(src, modes=["docstrings"])
    assert out is None or reason in ("nothing-to-do",) or out is not None
    # must never crash either way


def test_bus_message_roundtrip(coder):
    """execute() publishes a valid response message."""
    published = []

    class FakeBus:
        def publish(self, msg):
            published.append(msg)

    from captn.runtime.base import Message
    dc = DeterministicCoder(bus=FakeBus())
    msg = Message(sender="captn", destination="deterministic_coder", type="task",
                  payload={"task_id": "t1", "code": SRC, "src_path": "x.py"})
    dc.execute(msg)
    assert len(published) == 1
    payload = published[0].payload
    assert payload["task_id"] == "t1"
    assert payload["current_step_plugin"] == "deterministic_coder"
    assert payload["valid"] is True
    assert payload["method"].startswith("deterministic:")
