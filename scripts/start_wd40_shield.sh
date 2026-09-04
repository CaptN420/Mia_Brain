#!/bin/bash
# wd-40 Linux Shield Launcher
# Launches the daemon and system tray UI

WD40_DIR="$(dirname "$0")/wd-40"
cd "$WD40_DIR"

echo "=== wd-40 Linux Shield ==="
echo ""

# Function to check if daemon is running
is_daemon_running() {
    if [ -f "$WD40_DIR/.shield.pid" ]; then
        pid=$(cat "$WD40_DIR/.shield.pid")
        if kill -0 "$pid" 2>/dev/null; then
            return 0  # running
        fi
    fi
    return 1  # not running
}

# Function to start daemon
start_daemon() {
    if is_daemon_running; then
        echo "Shield daemon already running (PID $(cat $WD40_DIR/.shield.pid))"
        return 1
    fi
    
    echo "Starting wd-40 daemon..."
    # Start daemon in background, redirect output to log
    "$PYTHON" "$WD40_DIR/daemon_linux.py" --interval 60 >> "$WD40_DIR/logs/daemon.out" 2>&1 &
    DAEMON_PID=$!
    echo "$DAEMON_PID" > "$WD40_DIR/.shield.pid"
    
    # Wait briefly and check
    sleep 1
    if is_daemon_running; then
        echo "Daemon started successfully (PID $DAEMON_PID)"
        return 0
    else
        echo "ERROR: Failed to start daemon"
        rm -f "$WD40_DIR/.shield.pid"
        return 1
    fi
}

# Find Python
PYTHON="python3"
if command -v python3 &>/dev/null; then
    PYTHON="python3"
elif command -v python &>/dev/null; then
    PYTHON="python"
else
    echo "ERROR: No Python found"
    exit 1
fi

echo "Python: $PYTHON"
echo ""

# Start the daemon
start_daemon

echo ""
echo "Starting system tray UI..."
echo "  The tray icon will appear in your taskbar"
echo "  Right-click for: status, scan, findings, log, quit"
echo ""
echo "---"

# Start tray (pythonw if available, otherwise python with &)
if [[ ":$PATH:" == *":/usr/bin:"* ]] || command -v pythonw &>/dev/null; then
    $PYTHON -m pip install pystray pillow 2>/dev/null
    # Try pythonw first (macOS/Windows), fall back to python &
    if command -v pythonw &>/dev/null; then
        pythonw "$WD40_DIR/tray.py" &
    else
        # Use nohup with & for background
        nohup "$PYTHON" "$WD40_DIR/tray.py" > "$WD40_DIR/logs/tray_out.log" 2>&1 &
        echo "Tray started in background (PID $!)"
        echo "Check logs/tray_out.log for any issues"
    fi
else
    # Fallback: start in background
    nohup "$PYTHON" "$WD40_DIR/tray.py" > "$WD40_DIR/logs/tray_out.log" 2>&1 &
    echo "Tray started in background (PID $!)"
    echo "Check logs/tray_out.log for any issues"
fi

echo ""
echo "=== wd-40 Shield Active ==="
echo "  • Daemon running: PID $(cat $WD40_DIR/.shield.pid)"
echo "  • Tray UI: starting..."
echo "  • Use: right-click tray icon or run 'python wd-40/center.py status'"
echo "  • Stop: touch $WD40_DIR/stop.flag"
echo ""
