# wd-40 Linux — the shield

Linux port of the wd-40 protective layer for CaptN-BRAIN.

## Files Created (Linux Adaptation)

| Linux File | Original | Purpose |
|---|---|---|
| `watchdog_linux.py` | `watchdog.py` | Process & network monitoring via `/proc` |
| `autorespond_linux.py` | `autorespond.py` | Autonomous containment with iptables/nftables |
| `run_linux.py` | `run.py` | Sandbox runner with audit hooks |
| `daemon_linux.py` | `daemon.py` | Forever scanner loop |

## Prerequisites

```bash
# For hash reputation (ClamAV)
sudo apt-get update && sudo apt-get install -y clamav clamav-daemon
sudo freshclam

# For firewall containment (run as root for autorespond)
# iptables is standard; nftables is preferred on modern distros
sudo apt-get install -y iptables nftables
```

## Quick Start

```bash
# 1. Test watchdog (run as your user)
python wd-40/watchdog_linux.py

# 2. Test netcheck (run as your user)
python wd-40/netcheck.py

# 3. Test filecheck with ClamAV (run as your user)
python wd-40/filecheck.py --dir wd-40/sandbox

# 4. Run daemon (run as your user, no root needed for detection)
python wd-40/daemon_linux.py --interval 60

# 5. Enable autorespond (REQUIRES ROOT)
# Edit config.json:
#   { "autorespond_enabled": true, "firewall_contain": true }
sudo python wd-40/autorespond_linux.py _selftest_fw

# 6. Test sandbox runner
python wd-40/run_linux.py --net some_script.py
```

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
   watchdog_linux  netcheck.py  filecheck.py   run_linux.py  quarantine.py
   (/proc/net)     (abuse.ch)   (hash+ClamAV)  (audit hooks)  (human-approved
   (ps /proc)      feeds         reputation    sandbox       move/list/release
        └──────────────┴──────────┴───────────────┴──────────────┘
                                  │ findings -> logs/findings.jsonl
                                  ▼
                          ⚑ YOUR decision: quarantine move / release
                          (nothing is deleted or killed automatically)
```

## Key Differences from Windows Version

| Component | Windows | Linux |
|---|---|---|
| Process listing | `tasklist` | `/proc/<pid>/comm` |
| Network sockets | `netstat -ano` | `/proc/net/tcp`, `/proc/net/udp` |
| PID → path | `wmic` | `/proc/<pid>/exe` symlink |
| Firewall | `netsh advfirewall` | `iptables` / `nftables` |
| Kill process | `taskkill /F` | `kill -9` |
| Protected processes | Windows system names | Linux systemd/services names |
| Daemon window hiding | `CREATE_NO_WINDOW` | stdout redirect to log file |

## Config (config.json)

```json
{
  "_comment": "wd-40 shield options. Edit and RESTART the shield to apply.",
  "autorespond_enabled": false,
  "_autorespond_note": "Set true to enable autonomous containment (firewall block + quarantine on CONFIRMED hits, weird=contain-only). REQUIRES the shield to run ELEVATED (root). Kill-protection blocklist in autorespond_linux.py is ALWAYS enforced.",
  "firewall_contain": true,
  "_firewall_note": "If false, autorespond logs + findings but never touches the firewall."
}
```

## Logs

- `wd-40/logs/daemon.out` — daemon stdout/stderr
- `wd-40/logs/findings.jsonl` — timestamped detection events
- `wd-40/logs/autoactions.jsonl` — autorespond actions with undo info
- `wd-40/logs/baseline.json` — network baseline snapshot

## Stopping the Daemon

```bash
# Graceful stop (daemon checks this file each interval)
touch wd-40/stop.flag

# Or Ctrl+C if running in foreground
```

## Quarantine

```bash
# Move confirmed-bad file into quarantine
python wd-40/quarantine.py move /path/to/suspicious_file

# List quarantined items
python wd-40/quarantine.py list

# Restore (interactive confirmation)
python wd-40/quarantine.py release <name>
```

## Safety

- **Nothing is deleted, killed, or quarantined automatically** (unless autorespond enabled + root)
- Every detection is an append-only finding with UTC timestamp
- All state stays under `wd-40/` (except system temp dir for feeds)
- Kill-protection blocklist prevents bricking the system
- Firewall rules are OUTBOUND-only, named, logged, and reversible