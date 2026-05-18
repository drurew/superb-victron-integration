#!/bin/bash
# Deploy SuperB BMS driver to Victron Cerbo GX
# Run from the repository root on your development machine.
#
# Usage: ./scripts/install-to-cerbo.sh <cerbo-ip> [password]

set -e

if [ -z "$1" ]; then
    echo "Usage: $0 <cerbo-ip> [password]"
    exit 1
fi

CERBO_IP="$1"
CERBO_PASS="${2:-root}"

if ! command -v sshpass &> /dev/null; then
    echo "sshpass is required. Install it:"
    echo "  Fedora: sudo dnf install sshpass"
    echo "  Debian: sudo apt install sshpass"
    exit 1
fi

SSH="sshpass -p $CERBO_PASS ssh -o StrictHostKeyChecking=no root@$CERBO_IP"
SCP="sshpass -p $CERBO_PASS scp -o StrictHostKeyChecking=no"

echo "=== SuperB BMS Driver Installation ==="
echo "Target: $CERBO_IP"
echo ""

# Step 1: Copy source files
echo "[1/5] Copying source files..."
$SSH 'mkdir -p /data/bms/src /data/bms/docs /data/bms/scripts'
$SCP src/victron-bms.c src/victron_bms_multi.py \
     src/bms_canopen_client.py src/bms_firmware_updater.py \
     root@$CERBO_IP:/data/bms/src/
$SCP Makefile config.ini root@$CERBO_IP:/data/bms/
$SCP docs/DATASHEET.md docs/SuperB_Epsilon_V2.eds root@$CERBO_IP:/data/bms/docs/
echo "  Done."

# Step 2: Install build tools and compile
echo "[2/5] Installing build tools..."
$SSH 'opkg update && opkg install gcc gcc-symlinks binutils libgcc-s-dev'
echo "[3/5] Compiling C driver..."
$SSH 'cd /data/bms && make'
echo "  Done."

# Step 3: Configure CAN interface
echo "[4/5] Configuring CAN interface..."
$SSH '[ -f /etc/venus/canbus/vecan0 ] || echo "250000" > /etc/venus/canbus/vecan0'
echo "  Done."

# Step 4: Install service
echo "[5/5] Installing daemontools service..."
$SSH 'mkdir -p /service/victron-bms/log'
$SSH 'cat > /service/victron-bms/run << "RUNEOF"
#!/bin/sh
exec 2>&1
exec /data/bms/victron-bms vecan0
RUNEOF'
$SSH 'cat > /service/victron-bms/log/run << "LOGEOF"
#!/bin/sh
exec multilog t s25000 n4 /var/log/victron-bms
LOGEOF'
$SSH 'chmod +x /service/victron-bms/run /service/victron-bms/log/run'
echo "  Done."

# Wait for service to start
sleep 4

# Verify
echo ""
echo "=== Verification ==="
$SSH '
echo "Process:"
ps | grep victron-bms | grep -v grep || echo "  (starting up - check in a few seconds)"
echo ""
echo "Battery SOC:"
for i in 1 2 3; do
    soc=$(dbus -y com.victronenergy.battery.canopen_bms_node$i \
               /Soc GetValue 2>/dev/null || echo "not yet")
    echo "  Node $i: $soc%"
done
'

echo ""
echo "=== Installation Complete ==="
echo ""
echo "The driver starts automatically on boot."
echo ""
echo "Useful commands on the Cerbo:"
echo "  Status:   svstat /service/victron-bms"
echo "  Logs:     tail -f /var/log/victron-bms/current"
echo "  Restart:  svc -t /service/victron-bms"
echo "  Stop:     svc -d /service/victron-bms"
echo ""
echo "To set as system battery service:"
echo "  dbus -y com.victronenergy.settings /Settings/SystemSetup/BatteryService \\"
echo "    SetValue 'com.victronenergy.battery.canopen_bms_node1'"
