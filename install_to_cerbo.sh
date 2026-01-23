#!/bin/bash
# Installation script for SuperB Epsilon BMS integration with Victron Cerbo GX
# Run this from your development PC
#
# Usage: ./install_to_cerbo.sh <cerbo-ip> [password]

if [ -z "$1" ]; then
    echo "Usage: $0 <cerbo-ip> [password]"
    echo "Example: $0 <your-cerbo-ip> <your-password>"
    exit 1
fi

CERBO_IP="$1"
CERBO_PASS="${2:-root}"

echo "Installing SuperB BMS service to Cerbo GX at $CERBO_IP..."

# Check if sshpass is installed
if ! command -v sshpass &> /dev/null; then
    echo "ERROR: sshpass not found. Install it first:"
    echo "  Fedora/RHEL: sudo dnf install sshpass"
    echo "  Ubuntu/Debian: sudo apt install sshpass"
    exit 1
fi

# Copy all necessary files
echo "Copying Python files..."
sshpass -p "$CERBO_PASS" scp -o StrictHostKeyChecking=no \
    bms_canopen_client.py \
    bms_firmware_updater.py \
    bms_nmt.py \
    victron_bms_multi.py \
    config.ini \
    start_bms.sh \
    root@$CERBO_IP:/data/bms/ || { echo "Failed to copy files"; exit 1; }

# Install python3-can if not already installed
echo "Installing dependencies..."
sshpass -p "$CERBO_PASS" ssh -o StrictHostKeyChecking=no root@$CERBO_IP \
    'opkg list-installed | grep -q python3-can || (opkg update && opkg install python3-can)'

# Create config directory and update config for vecan0
echo "Setting up configuration..."
sshpass -p "$CERBO_PASS" ssh -o StrictHostKeyChecking=no root@$CERBO_IP \
    'mkdir -p /etc/victron-bms && sed "s/interface = can0/interface = vecan0/g" /data/bms/config.ini > /etc/victron-bms/config.ini && sed -i "s/interface = can0/interface = vecan0/g" /data/bms/config.ini'

# Make start script executable
echo "Setting up service scripts..."
sshpass -p "$CERBO_PASS" ssh -o StrictHostKeyChecking=no root@$CERBO_IP \
    'chmod +x /data/bms/start_bms.sh'

# Create auto-start in rc.local
echo "Creating startup script..."
sshpass -p "$CERBO_PASS" ssh -o StrictHostKeyChecking=no root@$CERBO_IP 'cat > /data/rc.local << "EOF"
#!/bin/bash
# Auto-start BMS service on boot
sleep 5
/data/bms/start_bms.sh
EOF
chmod +x /data/rc.local'

# Start the service now
echo "Starting BMS service..."
sshpass -p "$CERBO_PASS" ssh -o StrictHostKeyChecking=no root@$CERBO_IP \
    '/data/bms/start_bms.sh'

sleep 3

# Verify installation
echo ""
echo "Verifying installation..."
sshpass -p "$CERBO_PASS" ssh -o StrictHostKeyChecking=no root@$CERBO_IP \
    'if pgrep -f victron_bms > /dev/null; then echo "✅ Service is running"; tail -10 /var/log/bms.log | grep -E "registered|Node [0-9]"; else echo "❌ Service failed to start"; tail -20 /var/log/bms.log; exit 1; fi'

echo ""
echo "=========================================="
echo "Installation complete!"
echo "=========================================="
echo ""
echo "The service will start automatically on boot."
echo ""
echo "Useful commands:"
echo "  Check status:  ssh root@$CERBO_IP 'pgrep -f victron_bms'"
echo "  View logs:     ssh root@$CERBO_IP 'tail -f /var/log/bms.log'"
echo "  Restart:       ssh root@$CERBO_IP '/data/bms/start_bms.sh'"
echo "  Check config:  ssh root@$CERBO_IP 'cat /data/bms/config.ini'"
echo ""
echo "Set system battery (choose one):"
echo "  Node 1: dbus -y com.victronenergy.settings /Settings/SystemSetup/BatteryService SetValue 'com.victronenergy.battery.canopen_bms_node1'"
echo "  Node 2: dbus -y com.victronenergy.settings /Settings/SystemSetup/BatteryService SetValue 'com.victronenergy.battery.canopen_bms_node2'"
echo "  Node 3: dbus -y com.victronenergy.settings /Settings/SystemSetup/BatteryService SetValue 'com.victronenergy.battery.canopen_bms_node3'"
echo ""
