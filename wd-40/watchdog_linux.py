#!/usr/bin/env python3
"""wd-40 watchdog (Linux) — watches which programs are using the network.

Each run:
  1. snapshots every process with open TCP/UDP endpoints (via /proc/net)
  2. compares against the last snapshot (wd-40/logs/baseline.json)
  3. prints ONLY what is new or changed (stable output = silent when quiet)
     - new processes touching the network
     - processes that disappeared
     - new remote endpoints an existing process started talking to
  4. saves the new snapshot as baseline

Run manually:
    python wd-40/watchdog_linux.py            # one check, diff vs baseline
    python wd-40/watchdog_linux.py --reset    # adopt current state as clean baseline

Quarantine: nothing is killed automatically. When something looks weird,
it gets reported here and WE decide together — then it goes to
wd-40/quarantine (see quarantine.py).
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(HERE, "logs")
QUARANTINE = os.path.join(HERE, "quarantine")
BASELINE = os.path.join(LOGS, "baseline.json")
os.makedirs(LOGS, exist_ok=True)
os.makedirs(QUARANTINE, exist_ok=True)

# Local/private ranges we never flag as "remote"
PRIVATE = re.compile(
    r"^(127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|169\.254\.|\[::1\]|0\.0\.0\.0|\*:)")

# Well-known Linux system services allowed to talk out without being flagged
TRUSTED_NAMES = (
    "systemd", "systemd-resolve", "systemd-networkd", "sshd", "dhclient",
    "NetworkManager", "chronyd", "ntpd", "dnsmasq", "avahi-daemon",
    "dbus-daemon", "polkitd", "accounts-daemon", "gnome-keyring-daemon",
    "snapd", "containerd", "dockerd", "kubelet", "kube-proxy",
    "python", "python3", "bash", "zsh", "sh",
)

# Kernel threads (appear without comm in /proc/<pid>/comm)
KERNEL_THREADS = {
    "kthreadd", "ksoftirqd", "kworker", "rcu_sched", "rcu_bh",
    "migration", "watchdog", "khelper", "netns", "writeback",
    "crypto", "bioset", "kblockd", "md", "jbd2", "ext4",
}


def _pid_names():
    """PID -> process name via /proc/<pid>/comm (no admin needed)."""
    names = {}
    proc_dir = "/proc"
    if not os.path.isdir(proc_dir):
        return names
    try:
        for entry in os.listdir(proc_dir):
            if not entry.isdigit():
                continue
            pid = entry
            comm_path = os.path.join(proc_dir, pid, "comm")
            try:
                with open(comm_path, "r") as f:
                    name = f.read().strip()
                if name:
                    names[pid] = name
            except (OSError, IOError):
                continue
    except Exception:
        pass
    return names


def _parse_net_tcp():
    """Parse /proc/net/tcp and /proc/net/tcp6 for TCP connections."""
    connections = []
    for net_file in ("/proc/net/tcp", "/proc/net/tcp6"):
        if not os.path.exists(net_file):
            continue
        try:
            with open(net_file, "r") as f:
                lines = f.readlines()
        except (OSError, IOError):
            continue
        # Skip header line
        for line in lines[1:]:
            parts = line.strip().split()
            if len(parts) < 10:
                continue
            # Format: sl local_address rem_address st tx_queue rx_queue ...
            local_addr = parts[1]
            remote_addr = parts[2]
            state = parts[3]
            # Extract inode (used to find PID)
            inode = parts[9]
            connections.append((local_addr, remote_addr, state, inode, "TCP"))
    return connections


def _parse_net_udp():
    """Parse /proc/net/udp and /proc/net/udp6 for UDP connections."""
    connections = []
    for net_file in ("/proc/net/udp", "/proc/net/udp6"):
        if not os.path.exists(net_file):
            continue
        try:
            with open(net_file, "r") as f:
                lines = f.readlines()
        except (OSError, IOError):
            continue
        for line in lines[1:]:
            parts = line.strip().split()
            if len(parts) < 10:
                continue
            local_addr = parts[1]
            remote_addr = parts[2]
            inode = parts[9]
            connections.append((local_addr, remote_addr, "", inode, "UDP"))
    return connections


def _hex_to_ip_port(hex_addr, is_ipv6=False):
    """Convert hex address:port from /proc/net to readable IP:port."""
    try:
        if is_ipv6:
            # IPv6: 32 hex chars for address, 4 for port
            addr_hex = hex_addr[:32]
            port_hex = hex_addr[32:]
            # Format IPv6 address
            addr = ":".join(addr_hex[i:i+4] for i in range(0, 32, 4))
            # Compress zeros
            import socket
            try:
                addr = socket.inet_ntop(socket.AF_INET6, bytes.fromhex(addr_hex))
            except Exception:
                pass
        else:
            # IPv4: 8 hex chars for address (little-endian), 4 for port
            addr_hex = hex_addr[:8]
            port_hex = hex_addr[8:]
            # Convert from little-endian hex
            addr_bytes = bytes.fromhex(addr_hex)
            addr = ".".join(str(b) for b in reversed(addr_bytes))
        port = int(port_hex, 16)
        return f"{addr}:{port}"
    except Exception:
        return hex_addr


def _inode_to_pid(inode, pid_map):
    """Find PID that owns a given socket inode."""
    for pid in pid_map:
        fd_dir = f"/proc/{pid}/fd"
        if not os.path.isdir(fd_dir):
            continue
        try:
            for fd in os.listdir(fd_dir):
                fd_path = os.path.join(fd_dir, fd)
                try:
                    target = os.readlink(fd_path)
                    if f"socket:[{inode}]" in target:
                        return pid
                except (OSError, IOError):
                    continue
        except (OSError, IOError):
            continue
    return None


def netstat_rows():
    """Build process connection map from /proc/net and /proc/<pid>/fd."""
    names = _pid_names()
    pid_map = {pid: names.get(pid, "?") for pid in names}
    
    # Get all socket inodes and their connections
    tcp_conns = _parse_net_tcp()
    udp_conns = _parse_net_udp()
    
    procs = {}
    
    for local, remote, state, inode, proto in tcp_conns + udp_conns:
        pid = _inode_to_pid(inode, pid_map)
        if not pid:
            continue
        
        local_fmt = _hex_to_ip_port(local, ":" in local)
        remote_fmt = _hex_to_ip_port(remote, ":" in remote)
        
        procs.setdefault(pid, {"name": pid_map.get(pid, "?"), "conns": set()})
        procs[pid]["conns"].add((proto, local_fmt, remote_fmt, state))
    
    return {p: {"name": v["name"], "conns": sorted(v["conns"])} for p, v in procs.items()}


def remotes(conns):
    r = set()
    for proto, local, remote, state in conns:
        host = remote.rsplit(":", 1)[0]
        if PRIVATE.match(remote):
            continue
        r.add(f"{host}")
    return r


def main():
    reset = "--reset" in sys.argv
    snap = netstat_rows()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    lines = []
    if reset or not os.path.exists(BASELINE):
        with open(BASELINE, "w") as f:
            json.dump({"ts": now, "procs": snap}, f, indent=1)
        print(f"[wd-40] baseline set ({len(snap)} procs with sockets)"
              if reset else f"[wd-40] first run: baseline set ({len(snap)} procs)")
        return

    with open(BASELINE) as f:
        old = json.load(f)["procs"]

    old_pids, new_pids = set(old), set(snap)
    for pid in sorted(new_pids - old_pids, key=int):
        name = snap[pid]["name"]
        trusted = any(t in name.lower() for t in TRUSTED_NAMES) or name in KERNEL_THREADS
        tag = "" if trusted else "  <-- UNFAMILIAR"
        lines.append(f"NEW on network: [{pid}] {name}{tag}")
        for r in sorted(remotes(snap[pid]["conns"])):
            lines.append(f"    -> {r}")
    for pid in sorted(old_pids - new_pids, key=int):
        lines.append(f"GONE from network: [{pid}] {old[pid]['name']}")
    for pid in sorted(old_pids & new_pids):
        before = remotes(old[pid]["conns"])
        after = remotes(snap[pid]["conns"])
        fresh = after - before
        if fresh:
            lines.append(f"NEW endpoints: [{pid}] {snap[pid]['name']}")
            for r in sorted(fresh):
                lines.append(f"    -> {r}")

    with open(BASELINE, "w") as f:
        json.dump({"ts": now, "procs": snap}, f, indent=1)

    if lines:
        print(f"[wd-40] network changes at {now}")
        print("\n".join(lines))
    # silent when nothing changed


if __name__ == "__main__":
    main()