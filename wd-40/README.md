# wd-40 — the shield

`wd-40` is a protective layer for the CaptN-BRAIN environment.
Anything that runs from here is contained: it works *inside* this
folder and never leaks into the rest of the repo where the other
programs live.

Like its namesake, it loosens things up and keeps rust out — it does
not leave residue on the neighbor modules (`captn/`, `mia/`, `alchimie/`,
`tools/`, `scripts/`), which stay read-only from the shield's perspective.

## Architecture

```
                  ┌─────────────────────────────────────────┐
   sensors        │            wd-40 Security Center         │
  (run forever)   │                 center.py                │
                  │      status | scan | report | feed-age   │
                  └───────────────┬─────────────────────────┘
                                  │ drives
        ┌──────────────┬──────────┼───────────────┬──────────────┐
        ▼              ▼          ▼               ▼              ▼
   watchdog.py   netcheck.py  filecheck.py   run.py        quarantine.py
   who's online  C2/malware   hash + ClamAV  sandbox       human-approved
   (netstat)     hosts?       reputation     runner        move/list/release
        └──────────────┴──────────┴───────────────┴──────────────┘
                                  │ findings -> logs/findings.jsonl
                                  ▼
                          ⚑ YOUR decision: quarantine move / release
                          (nothing is deleted or killed automatically)
```

## The looping scanner (always running)

`daemon.py` is the self-contained fork that runs the whole chain forever
on an interval. It never exits on its own.

```bash
python wd-40/daemon.py                 # loop forever, 300s between passes
python wd-40/daemon.py --interval 120  # every 2 minutes
python wd-40/daemon.py --once          # single pass (for tests)
```

Stop it any time by creating the stop flag (no SIGKILL needed):

```bash
echo stop > wd-40/stop.flag
```

The daemon writes its live log to `wd-40/logs/daemon.out` and appends
every detection to `wd-40/logs/findings.jsonl` with a UTC timestamp.

## Tools

| File | Role |
|------|------|
| `run.py` | Sandbox runner. Forces cwd into `wd-40/sandbox` and installs a `sys.addaudithook`. By default it **blocks any write or subprocess spawn outside `wd-40/` or the temp dir** (uses `realpath` to defeat symlink escapes) AND **blocks reads of secrets** (`.captn/`, `.env`, private keys) so a contained script cannot steal or exfiltrate credentials. Network egress (`socket`/`connect`) is **blocked** unless `--net` is passed. Use `--strict` to flip reads to an allow-list (only the shield, temp, and explicit `--allow <path>` are readable); `--net` re-enables egress. |
| `watchdog.py` | Snapshots every process with open TCP/UDP sockets (`netstat -ano` + `tasklist`, no admin needed), diffs against `logs/baseline.json`, flags new / unfamiliar processes and new remote endpoints. |
| `netcheck.py` | Cross-checks IPs/hosts against **abuse.ch Feodo Tracker** (C2 IPs) and **URLhaus** (malware hosts). Feeds cached 30 min in `feeds/`. |
| `filecheck.py` | Hashes files (SHA-256) and matches against the **MalwareBazaar / URLhaus payload** feed (no API key). Runs **ClamAV** too, if installed. |
| `quarantine.py` | Human-approved quarantine ONLY. `move` relocates a confirmed-bad file; `release` restores it after an explicit `y/N` prompt. Never auto-deletes. |
| `center.py` | One hub: `status`, `scan`, `report`, `feed-age`. |

## Open-source detection sources (most recent)
- **abuse.ch Feodo Tracker** — botnet C2 server IPs (every 5 min)
- **abuse.ch URLhaus** — malware distribution URLs/hosts
- **MalwareBazaar** payload SHA-256 export — known-bad hashes
- **ClamAV** signatures (optional, see `clamav-setup.md`) for on-disk deep scan

Feeds are refreshed automatically by the tools (cached locally, max 30 min old).

## Safety guarantees
1. **Isolation** — scripts/caches/temp stay under `wd-40/`.
2. **Neighbors untouched** — the rest of the repo is read-only from here.
3. **No autonomous action** — detections are reported; quarantine/removal
   requires an explicit human `y`.
4. **No kills** — `wd-40` never terminates a process; it reports and we decide.
5. **Secret-safe sandbox** — `run.py` is a *confinement* boundary, not just an
   integrity boundary: a script launched through it cannot read `.captn/`,
   `.env`, or private keys, and cannot open outbound sockets. (Caveat: audit
   hooks are per-interpreter; a spawned child still runs un-hooked, which is why
   spawns outside the shield are blocked.)

## Running untrusted code through the sandbox
```bash
# default: writes/spawns outside shield blocked, secrets unreadable, no net
python wd-40/run.py scripts/some_llm_generated_thing.py

# crawler that needs the internet -> explicitly allow egress
python wd-40/run.py --net captn/workers/crawler.py

# max isolation: only the shield + an explicit read folder are visible
python wd-40/run.py --strict --allow ../crawled scripts/probe.py

# self-test the shield itself
python wd-40/_selftest.py
```

## Layout
```
wd-40/
  run.py  watchdog.py  netcheck.py  filecheck.py  quarantine.py  center.py  daemon.py
  _selftest.py  # asserts the sandbox blocks writes/secrets/net/spawns
  feeds/      # cached abuse.ch intel
  logs/       # baseline.json, findings.jsonl, daemon.out
  sandbox/    # where sandboxed scripts execute (chdir target)
  quarantine/ # moved (confirmed-bad) files + log.jsonl
```
