#!/usr/bin/env python3
"""wd-40 guard runner (Linux).

Runs a target script *inside* the shield: cwd is forced to
wd-40/sandbox and an audit hook blocks any attempt to:

  * open files for WRITING outside the shield (wd-40/) or the system temp dir
  * spawn a subprocess / executable that is not itself inside the shield or temp
  * READ your SECRETS (.captn/, .env, private keys) -- default deny-list
        (use --strict to flip to an allow-list: reads only under the shield,
         temp, or an explicit --allow <path>)
  * make OUTBOUND network connections (unless --net is given)

The point: a script launched through run.py cannot tamper with the rest of the
repo, cannot read your credentials, and cannot phone home with them.

Usage:
    python wd-40/run_linux.py <script.py> [args...]
    python wd-40/run_linux.py --strict --allow ../crawled <script.py>   # max isolation
    python wd-40/run_linux.py --net <trusted_crawler.py>                 # allow egress
"""
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHIELD = os.path.join(REPO, "wd-40")
SANDBOX = os.path.join(SHIELD, "sandbox")
LOGS = os.path.join(SHIELD, "logs")
os.makedirs(SANDBOX, exist_ok=True)
os.makedirs(LOGS, exist_ok=True)

# resolve these ONCE, before the hook is installed (hook calls would recurse)
SHIELD_REAL = os.path.realpath(SHIELD)
TEMP_REAL = os.path.realpath(tempfile.gettempdir())
_hook_busy = False

# The running interpreter MUST be allowed to read its own core files (stdlib,
# site-packages, the executable itself) or it cannot even import. A sandbox
# hook fires for those internal reads too, so we whitelist them up front.
CORE_ROOTS = []
for _p in (getattr(sys, "prefix", None), getattr(sys, "base_prefix", None),
          getattr(sys, "base_exec_prefix", None)):
    if _p:
        CORE_ROOTS.append(os.path.realpath(_p))
_exe = os.path.realpath(sys.executable)
CORE_ROOTS.append(os.path.dirname(_exe))
CORE_ROOTS.append(os.path.dirname(_exe) + os.sep)
# dedupe
CORE_ROOTS = sorted({r for r in CORE_ROOTS}, key=len)


# --------------------------------------------------------------------------
# Secret protection (read deny-list, default mode)
# --------------------------------------------------------------------------
# Paths / filenames we must NEVER let a sandboxed script read, even though it
# can otherwise read the repo freely. This is what keeps .captn/ safe.
SECRET_ROOTS = [os.path.realpath(os.path.join(REPO, ".captn"))]
SECRET_BASENAMES = {
    ".env", "auth_token", "openai_config.json", "credentials.json",
    "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa",
}
SECRET_SUFFIXES = (".pem", ".key", ".pfx", ".p12", ".keystore")


def _is_secret_path(real):
    for root in SECRET_ROOTS:
        if real == root or real.startswith(root + os.sep):
            return True
    base = os.path.basename(real)
    if base in SECRET_BASENAMES:
        return True
    if base.startswith(".env."):
        return True
    for suf in SECRET_SUFFIXES:
        if base.endswith(suf):
            return True
    return False


# --------------------------------------------------------------------------
# Runtime policy (set from CLI flags in main)
# --------------------------------------------------------------------------
# In --strict mode reads are allowed ONLY under the shield, temp, or an
# explicit --allow list. Otherwise (default) reads are allowed everywhere
# EXCEPT the secret paths above.
STRICT = False
ALLOW_INCLUDES = []
NET_OK = False


def _inside_shield(path):
    """True if path is inside wd-40/ or the system temp directory.

    Uses realpath to defeat symlink escapes (a scan using abspath would let a
    symlink inside the shield point at a file outside it).
    """
    if _hook_busy:
        return True  # never raise from inside our own bookkeeping
    try:
        real = os.path.realpath(path)
    except OSError:
        return False  # can't resolve -> refuse
    return real.startswith(SHIELD_REAL + os.sep) or real.startswith(TEMP_REAL + os.sep)


def _read_allowed(path):
    """Read policy. Returns True if the read is permitted.

    Always permits the running interpreter's own core files (stdlib, venv,
    executable) so imports don't break. Then enforces the secret deny-list,
    then the strict allow-list. Any unexpected error fails OPEN (allow) so a
    weird internal read can never crash the interpreter.
    """
    try:
        real = os.path.realpath(path)
    except OSError:
        return False
    # core files the interpreter needs to function
    for root in CORE_ROOTS:
        if real == root or real.startswith(root + os.sep) \
                or real.startswith(root):
            return True
    if STRICT:
        if _inside_shield(real):
            return True
        for inc in ALLOW_INCLUDES:
            if real == inc or real.startswith(inc + os.sep):
                return True
        return False
    # default mode: allow everything that is not a secret
    return not _is_secret_path(real)


def _is_read_open(event, args):
    """Robustly detect a read-only open() / os.open() from audit args."""
    if event != "open":
        return False
    if len(args) < 2:
        return False
    mode_or_flags = args[1]
    if isinstance(mode_or_flags, str):
        # builtin open(): mode string like "r", "rt", "rb"
        return "r" in mode_or_flags and "+" not in mode_or_flags
    if isinstance(mode_or_flags, int):
        # os.open(): flags bitmask. Read-only iff none of the write bits set.
        return not (mode_or_flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT
                                     | os.O_APPEND | os.O_TRUNC))
    # unknown shape -> do not treat as a safe read
    return False


# Executing another interpreter/process escapes the hook entirely (audit hooks
# are per-interpreter and NOT inherited across subprocess). Inside the sandbox
# we therefore block spawning anything that is not itself inside the shield or
# the system temp dir. Pure-Python sandboxed scripts only.
_BLOCKED_SPAWN_EVENTS = ("subprocess.Popen", "os.system", "os.popen",
                         "os.exec", "os.posix_spawn")

# Network egress. A contained script must not be able to phone home with
# secrets. We block socket creation/connection/listening unless --net is set.
# (DNS resolution via getaddrinfo is a libc call with no clean audit hook, but
#  a resolved address is useless once connect() is blocked.)
_BLOCKED_NET_EVENTS = ("socket.socket", "socket.connect", "socket.connect_ex",
                       "socket.bind", "socket.listen")


def _audit(event, args):
    # The hook also fires for run.py's own internal reads/writes while it is
    # doing this bookkeeping. Never block or raise from inside ourself.
    if _hook_busy:
        return
    try:
        _audit_inner(event, args)
    except PermissionError:
        raise  # our own deliberate blocks
    except Exception:
        # Any unexpected error must NOT crash the interpreter. Fail open:
        # we already deny secrets/writes/net explicitly above; an exception
        # here means our policy logic hit something unforeseen, so allow it
        # rather than taking down the whole sandboxed run.
        return


def _is_interpreter_cache_write(real):
    """Allow the interpreter to write its own bytecode caches.

    During import CPython writes .pyc files into Lib/__pycache__ under its
    install dir. That is benign (not an escape, not a secret) but it happens
    OUTSIDE the shield, so we must whitelist exactly that pattern -- otherwise
    every run drowns in BLOCKED .pyc noise. We only allow writes that are
    clearly bytecode caches inside a core (interpreter) root.
    """
    base = os.path.basename(real)
    if not (base.endswith(".pyc") or "__pycache__" in real.replace("/", os.sep).split(os.sep)):
        return False
    for root in CORE_ROOTS:
        if real == root or real.startswith(root + os.sep):
            return True
    return False


def _audit_inner(event, args):
    # --- network egress ---
    if not NET_OK and event in _BLOCKED_NET_EVENTS:
        sys.stderr.write(f"[wd-40] BLOCKED network: {event}\n")
        raise PermissionError(f"wd-40 shield: network blocked ({event})")

    # --- subprocess / exec escapes ---
    if event in _BLOCKED_SPAWN_EVENTS:
        cmds = []
        if isinstance(args, (list, tuple)) and len(args) >= 2:
            exe, cmd_arg = args[0], args[1]
            if isinstance(exe, str):
                cmds.append(exe)
            if isinstance(cmd_arg, (list, tuple)):
                cmds += [c for c in cmd_arg if isinstance(c, str)]
            elif isinstance(cmd_arg, str):
                cmds.append(cmd_arg)
        for cmd in cmds:
            real = os.path.realpath(cmd)
            # Allow only executables that genuinely live inside the shield or
            # the temp dir. A bare name like "notepad.exe" resolves relative to
            # cwd (sandbox) and exists nowhere -> blocked as an escape.
            if not (real.startswith(SHIELD_REAL + os.sep)
                    or real.startswith(TEMP_REAL + os.sep)) \
                    or not os.path.exists(real):
                sys.stderr.write(
                    f"[wd-40] BLOCKED spawn outside shield: {event}: {cmd!r}\n")
                raise PermissionError(f"wd-40 shield: spawn blocked: {cmd!r}")
        return

    # --- filesystem writes + reads ---
    if event in ("open", "os.remove", "os.rename", "os.replace", "os.rmdir",
                 "os.removedirs", "shutil.rmtree", "os.link", "os.symlink"):
        if event == "open":
            if _is_read_open(event, args):
                if not _read_allowed(args[0]):
                    sys.stderr.write(
                        f"[wd-40] BLOCKED read of secret: {args[0]}\n")
                    raise PermissionError(
                        f"wd-40 shield: read blocked: {args[0]}")
                return
            # write-open: the resolved path must stay in the shield
            paths = [args[0]] if isinstance(args[0], str) else []
        elif event in ("os.rename", "os.replace", "os.link"):
            # BOTH endpoints must stay inside the shield, otherwise a script
            # could move / hardlink a file OUT of the sandbox (escape).
            paths = [a for a in args[:2] if isinstance(a, str)]
        elif event == "os.symlink":
            # only the link's own location is created here; following the link
            # is caught later because _inside_shield() realpaths through it.
            paths = [args[1]] if len(args) > 1 and isinstance(args[1], str) else []
        else:  # os.remove / os.rmdir / os.removedirs / shutil.rmtree
            paths = [args[0]] if isinstance(args[0], str) else []
        for path in paths:
            if path and not _inside_shield(path):
                real = os.path.realpath(path)
                # benign: the interpreter writing its own .pyc caches outside
                # the shield (e.g. its install dir). Not an escape, not a secret.
                if _is_interpreter_cache_write(real):
                    continue
                sys.stderr.write(
                    f"[wd-40] BLOCKED write outside shield: {event}: {path}\n")
                raise PermissionError(f"wd-40 shield: write blocked: {path}")


def main():
    argv = sys.argv[1:]
    flags = {"--net": False, "--strict": False}
    allow = []
    rest = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--net":
            flags["--net"] = True
        elif a == "--strict":
            flags["--strict"] = True
        elif a == "--allow":
            i += 1
            if i < len(argv):
                allow.append(os.path.realpath(argv[i]))
        else:
            rest.append(a)
        i += 1

    if not rest:
        print(__doc__)
        return 2

    global STRICT, NET_OK, ALLOW_INCLUDES
    STRICT = flags["--strict"]
    NET_OK = flags["--net"]
    ALLOW_INCLUDES = allow

    target = os.path.abspath(rest[0])
    # sys.argv[0] becomes the script; the rest are its real arguments
    sys.argv = rest

    os.chdir(SANDBOX)
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    # Don't write .pyc caches at all inside the sandbox (less noise, and the
    # interpreter won't try to write outside the shield during import).
    sys.dont_write_bytecode = True

    sys.addaudithook(_audit)

    # DNS exfiltration tripwire: socket.getaddrinfo/gethostbyname raise NO audit
    # event, so a script could encode secrets into a hostname and resolve it even
    # with egress off. With --net unset we monkeypatch them to deny any lookup
    # that isn't localhost/loopback. (This is defense-in-depth, not a hard
    # boundary -- see the ctypes bypass note below.)
    if not NET_OK:
        import socket as _socket
        _real_getaddrinfo = _socket.getaddrinfo
        _real_gethostbyname = _socket.gethostbyname
        _real_gethostbyname_ex = _socket.gethostbyname_ex

        def _ga_guard(host, *a, **kw):
            h = (host or "").lower()
            if h in ("localhost", "127.0.0.1", "::1", "ip6-localhost") \
                    or h.startswith("127.") or h.startswith("0.0.0.0"):
                return _real_getaddrinfo(host, *a, **kw)
            raise PermissionError(f"wd-40 shield: DNS blocked: {host!r}")

        def _ghn_guard(host):
            h = (host or "").lower()
            if h in ("localhost", "127.0.0.1", "::1") or h.startswith("127."):
                return _real_gethostbyname(host)
            raise PermissionError(f"wd-40 shield: DNS blocked: {host!r}")

        _socket.getaddrinfo = _ga_guard
        _socket.gethostbyname = _ghn_guard
        _socket.gethostbyname_ex = lambda host: _ghn_guard(host) or _real_gethostbyname_ex(host)

    # NOTE ON LIMITS: sys.addaudithook is NOT a hard security boundary. A script
    # can call os.open via ctypes, or reassign builtins, bypassing every check
    # here. The real containment is the container (non-root, read-only rootfs,
    # egress firewall). This runner is a tripwire against naive / LLM-generated
    # scripts, which is the intended use.

    import runpy
    try:
        runpy.run_path(target, run_name="__main__")
    except PermissionError as e:
        print(e, file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())