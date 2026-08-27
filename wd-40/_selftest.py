"""Self-test for the wd-40 sandbox (run.py).

Launches a set of probes THROUGH run.py and asserts the shield behaves.
Each probe is a tiny script that tries one forbidden/allowed action.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WD40 = HERE
PY = sys.executable
RUN = os.path.join(WD40, "run.py")

PROBES = {}

PROBES["write_outside"] = '''
import os
p = os.path.join(os.path.dirname(os.path.dirname(os.getcwd())), "captn", "_PWN.txt")
try:
    open(p, "w").write("x")
    print("RESULT FAIL: wrote outside")
except PermissionError:
    print("RESULT OK: write blocked")
'''

PROBES["read_secret"] = '''
import os
p = os.path.join(os.path.dirname(os.path.dirname(os.getcwd())), ".captn", "auth_token")
try:
    data = open(p, "r").read()
    print("RESULT FAIL: read secret:", repr(data[:8]))
except PermissionError:
    print("RESULT OK: secret read blocked")
except OSError:
    print("RESULT OK: secret read blocked (open error)")
'''

PROBES["read_repo_normal"] = '''
import os
p = os.path.join(os.path.dirname(os.path.dirname(os.getcwd())), "requirements.txt")
try:
    data = open(p, "r").read()[:8]
    print("RESULT OK: normal read:", repr(data))
except PermissionError:
    print("RESULT FAIL: normal read blocked")
except (OSError, FileNotFoundError):
    print("RESULT OK: normal read (file absent, not blocked)")
'''

PROBES["net_egress"] = '''
import socket
try:
    s = socket.socket(); s.connect(("8.8.8.8", 53))
    print("RESULT FAIL: net allowed")
except PermissionError:
    print("RESULT OK: net blocked")
except OSError:
    print("RESULT OK: net blocked (os)")
'''

PROBES["spawn_outside"] = '''
import subprocess
try:
    subprocess.Popen(["calc.exe"])
    print("RESULT FAIL: spawn allowed")
except PermissionError:
    print("RESULT OK: spawn blocked")
'''

PROBES["net_allowed_with_flag"] = '''
import socket
try:
    s = socket.create_connection(("1.1.1.1", 53), timeout=3)
    s.close()
    print("RESULT OK: net allowed with --net")
except PermissionError:
    print("RESULT FAIL: net wrongly blocked with --net")
except OSError:
    print("RESULT OK: net allowed with --net (os error, not hook)")
'''

PROBES["strict_read_denied"] = '''
import os
p = os.path.join(os.path.dirname(os.path.dirname(os.getcwd())), "requirements.txt")
try:
    open(p, "r").read()
    print("RESULT FAIL: strict allowed repo read")
except PermissionError:
    print("RESULT OK: strict blocked repo read")
'''

PROBES["strict_allow_read_ok"] = '''
import os
p = os.path.join(os.path.dirname(os.path.dirname(os.getcwd())), "crawled", "_probe.txt")
try:
    data = open(p, "r").read()
    print("RESULT OK: --allow read worked:", repr(data))
except PermissionError:
    print("RESULT FAIL: --allow read blocked")
'''

PROBES["rename_outside"] = '''
import os
src = os.path.join(os.getcwd(), "_esc_src.txt")
dst = os.path.join(os.path.dirname(os.path.dirname(os.getcwd())), "captn", "_PWN_RENAME.txt")
open(src, "w").write("x")
try:
    os.rename(src, dst)
    print("RESULT FAIL: rename escaped shield")
except PermissionError:
    print("RESULT OK: rename outside blocked")
'''

PROBES["dns_exfil"] = '''
import socket
try:
    socket.getaddrinfo("evil.example", 80)
    print("RESULT FAIL: dns allowed")
except PermissionError:
    print("RESULT OK: dns blocked")
'''

PROBES["dns_local_ok"] = '''
import socket
try:
    socket.getaddrinfo("localhost", 80)
    print("RESULT OK: localhost dns allowed")
except PermissionError:
    print("RESULT FAIL: localhost dns blocked")
'''


def write_probe(name, code):
    path = os.path.join(WD40, "_probe_" + name + ".py")
    with open(path, "w") as f:
        f.write("# -*- coding: utf-8 -*-\n" + code)
    return path


def run_through(path, extra=None):
    cmd = [PY, RUN] + (extra or []) + [path]
    env = dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1")
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             cwd=WD40, env=env, timeout=60)
    except subprocess.TimeoutExpired:
        return "TIMEOUT"
    return (out.stdout + out.stderr).strip()


def find_result(text):
    for line in text.splitlines():
        if line.startswith("RESULT"):
            return line
    return "NO RESULT: " + text[:200]


def main():
    cases = [
        ("write_outside", [], "write blocked"),
        ("read_secret", [], "secret read blocked"),
        ("read_repo_normal", [], "normal read"),
        ("net_egress", [], "net blocked"),
        ("spawn_outside", [], "spawn blocked"),
        ("net_allowed_with_flag", ["--net"], "net allowed with --net"),
        ("strict_read_denied", ["--strict"], "strict blocked repo read"),
        ("strict_allow_read_ok", ["--strict", "--allow", os.path.join(REPO, "crawled")], "allow read worked"),
        ("rename_outside", [], "rename outside blocked"),
        ("dns_exfil", [], "dns blocked"),
        ("dns_local_ok", [], "localhost dns allowed"),
    ]
    passed = 0
    total = len(cases)
    print("=== wd-40 sandbox self-test ===")
    for name, extra, expect in cases:
        p = write_probe(name, PROBES[name])
        if name == "strict_allow_read_ok":
            cdir = os.path.join(REPO, "crawled")
            os.makedirs(cdir, exist_ok=True)
            with open(os.path.join(cdir, "_probe.txt"), "w") as f:
                f.write("allowed-read-target")
        out = run_through(p, extra)
        res = find_result(out)
        ok = expect in res
        passed += 1 if ok else 0
        print(f"[{'PASS' if ok else 'FAIL'}] {name:22} -> {res}")
    print(f"\n=== {passed}/{total} passed ===")
    for name in PROBES:
        try:
            os.remove(os.path.join(WD40, "_probe_" + name + ".py"))
        except OSError:
            pass
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
