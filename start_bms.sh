#!/bin/sh
# BMS Service Wrapper - ensures service stays running
# This script properly daemonizes the Python service

cd /data/bms || exit 1

# Kill any existing instances
pkill -f "python3 victron_bms_multi.py" 2>/dev/null

# Wait a moment for cleanup
sleep 1

# Start service with proper daemonization:
# - setsid: creates new session (detaches from terminal)
# - </dev/null: redirect stdin from /dev/null
# - >/var/log/bms.log: redirect stdout to log
# - 2>&1: redirect stderr to stdout
# - & disown: background and remove from job control

nohup setsid python3 -u victron_bms_multi.py </dev/null >/var/log/bms.log 2>&1 &

# Give it a moment to start
sleep 2

# Check if it's running
if pgrep -f "python3.*victron_bms_multi" > /dev/null; then
    echo "BMS service started successfully"
    tail -5 /var/log/bms.log
    exit 0
else
    echo "ERROR: BMS service failed to start"
    tail -10 /var/log/bms.log
    exit 1
fi
