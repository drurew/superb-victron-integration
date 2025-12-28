# Victron BMS CANopen Integration

✅ **PRODUCTION READY** - Fully tested and deployed on Victron Cerbo GX

Complete plug-and-play integration for SuperB Epsilon V2 BMS with Victron Cerbo GX via CANopen.

## Overview

This service allows SuperB Epsilon V2 battery management systems to appear as native battery monitors in the Victron Venus OS ecosystem. It reads battery data via CANopen (CAN bus) and publishes it to the Victron D-Bus interface.

**Status:** Deployed and working on live system (December 2025)  
**Batteries:** Supports multiple batteries (tested with 3× 150Ah)  
**Update Rate:** 1 second (real-time monitoring)

## Features

- ✅ **Native Victron Integration**: Appears as standard battery monitor in Venus OS
- ✅ **Multi-Battery Support**: Monitor multiple batteries individually
- ✅ **Real-time Monitoring**: 1-second update interval for live data
- ✅ **Comprehensive Data**: Voltage, current, SOC, temperature, charge cycles
- ✅ **Automatic Startup**: Auto-starts on Cerbo boot via `/data/rc.local`
- ✅ **Robust Operation**: Survives SSH disconnect, handles errors gracefully
- ✅ **Verified SDO Objects**: All conversions tested on live hardware
- ⚠️ **Individual Mode Only**: Aggregate mode (combining batteries) not yet implemented

## Hardware Requirements

- Victron Cerbo GX (or other Venus OS device)
- SuperB Epsilon V2 BMS with CANopen support
- Cerbo GX built-in VE.Can port (no external CAN adapter needed!)
- CAN cable connecting BMS to Cerbo's VE.Can port

## Verified BMS Data

All SDO conversions have been verified against live hardware:

| Parameter | SDO Index | Conversion | Unit | Status |
|-----------|-----------|------------|------|--------|
| Battery Voltage | 0x6060:00 | ÷1024 | V | ✓ Verified |
| State of Charge | 0x6081:00 | None | % | ✓ Verified |
| Temperature | 0x6010:00 | ÷8 | °C | ✓ Verified |
| Current | 0x2010:00 | ÷1000 | A | ✓ Verified (signed, mA) |
| Charge Cycles | 0x6050:00 | None | - | ✓ Verified |
| Ah Since Equalization | 0x6053:00 | ÷8 | Ah | ✓ Verified |

**Note**: Current uses manufacturer-specific object 0x2010:00 (not 0x6070 which is unsigned charge-only).

## Installation

### Quick Install on Cerbo GX

**Prerequisites:**
- Cerbo GX with SSH access enabled
- BMS connected to Cerbo's VE.Can port (vecan0)
- `sshpass` installed on your PC (`sudo dnf install sshpass` or `sudo apt install sshpass`)

**One-Command Installation:**

```bash
chmod +x install_to_cerbo.sh
./install_to_cerbo.sh <cerbo-ip> [password]

# Example:
./install_to_cerbo.sh <your-cerbo-ip> <your-password>
```

The installer will:
1. Copy all files to `/data/bms/` on Cerbo
2. Install `python3-can` via opkg (if needed)
3. Fix config to use `vecan0` (not `can0`)
4. Set up auto-start in `/data/rc.local`
5. Start the service immediately

### Manual Installation

If the automated installer doesn't work, follow these steps:

1. **Copy files to Cerbo:**
   ```bash
   scp -r * root@<cerbo-ip>:/data/bms/
   ```

2. **SSH into Cerbo:**
   ```bash
   ssh root@<cerbo-ip>
   ```

3. **Fix config for Cerbo's native CAN interface:**
   ```bash
   cd /data/bms
   sed -i 's/interface = can0/interface = vecan0/g' config.ini
   mkdir -p /etc/victron-bms
   cp config.ini /etc/victron-bms/
   ```

4. **Make scripts executable:**
   ```bash
   chmod +x start_bms.sh
   ```

5. **Create auto-start:**
   ```bash
   cat > /data/rc.local << 'EOF'
#!/bin/bash
sleep 5
/data/bms/start_bms.sh
EOF
   chmod +x /data/rc.local
   ```

6. **Start service:**
   ```bash
   /data/bms/start_bms.sh
   ```

### Verification

Check if service is running:

```bash
# Check process
pgrep -f victron_bms

# View logs
tail -f /var/log/bms.log

# Should see:
# registered ourselves on D-Bus as com.victronenergy.battery.canopen_bms_node1
# Node 1 voltage: raw=13562, converted=13.24
```

Check D-Bus registration:

```bash
dbus -y com.victronenergy.battery.canopen_bms_node1 /ProductName GetText
# Output: 'SuperB Epsilon V2 BMS (Node 1)'
```

Set as system battery (choose one node):

```bash
dbus -y com.victronenergy.settings /Settings/SystemSetup/BatteryService SetValue 'com.victronenergy.battery.canopen_bms_node1'
```

## Configuration

### Critical Settings

Edit `/data/bms/config.ini`:

```ini
[CAN]
interface = vecan0        # ✅ CRITICAL: Must be vecan0 on Cerbo (not can0!)
bitrate = 250000          # Standard for SuperB BMS
node_ids = 1,2,3         # Comma-separated list of BMS node IDs

[Victron]
service_name_prefix = com.victronenergy.battery.canopen_bms
device_instance_start = 1
product_name = SuperB Epsilon V2 BMS
update_interval = 1.0     # Update every 1 second
mode = individual         # Show each battery separately (aggregate not implemented)

[Battery]
capacity = 150            # Per-battery capacity in Ah
chemistry = LiFePO4
number_of_cells = 4

[Victron]
service_name = com.victronenergy.battery.canopen_bms
device_instance = 1       # Unique device ID in Victron system
product_name = SuperB Epsilon V2 BMS
update_interval = 1.0     # Update frequency in seconds

[Battery]
capacity = 200            # Battery capacity in Ah
chemistry = LiFePO4       # Battery chemistry
number_of_cells = 4       # Number of cells in series
```

## CAN Interface Setup

The installer automatically configures the CAN interface. Manual setup:

```bash
# Load kernel modules
modprobe can
modprobe can_raw
modprobe can_dev
modprobe ix_usb_can  # or appropriate driver for your USB-CAN adapter

# Configure interface
ip link set can0 down
ip link set can0 type can bitrate 250000
ip link set can0 up

# Verify
ip link show can0
```

## Victron Integration

After installation, the BMS will appear in:

1. **Victron Remote Console**: 
   - Navigate to Settings → System Setup → Battery Monitor
   - Select the SuperB Epsilon V2 BMS

2. **VRM Portal**:
   - Battery data visible in online dashboard
   - Historical data logging

3. **D-Bus Paths**:
   ```
   com.victronenergy.battery.canopen_bms
   ├── /Dc/0/Voltage
   ├── /Dc/0/Current
   ├── /Dc/0/Power
   ├── /Dc/0/Temperature
   ├── /Soc
   ├── /History/ChargeCycles
   └── ... (see victron_bms_dbus.py for complete list)
   ```

## Troubleshooting

### Service won't start

```bash
# Check service status
systemctl status victron-bms

# View logs
journalctl -u victron-bms -n 50

# Check CAN interface
ip link show can0
candump can0  # Should show CAN traffic
```

### No data from BMS

```bash
# Verify BMS is responding
cansend can0 602#4000100000000000  # Request device type from node 2
candump can0 -n 1                  # Should see response
```

### D-Bus not updating

```bash
# Check D-Bus service
dbus -y com.victronenergy.battery.canopen_bms /Dc/0/Voltage GetValue

# Monitor D-Bus
dbus-monitor "sender='com.victronenergy.battery.canopen_bms'"
```

## File Structure

```
victron-bms/
├── bms_canopen_client.py      # CANopen SDO client
├── victron_bms_dbus.py        # Main D-Bus service
├── config.ini                  # Configuration file
├── victron-bms.service        # Systemd service unit
├── install.sh                 # Installation script
├── test_local.py              # Local testing tool
└── README.md                  # This file
```

## Dependencies

- Python 3.7+
- python-can
- Victron velib_python (included in Venus OS)
- SocketCAN kernel support

## Firmware Compatibility

- **Firmware v1.0**: Core monitoring (voltage, SOC, current, temperature)
- **Firmware v1.2+**: Additional features (Ah Expended, Ah Returned)

SDO indices 0x6051 and 0x6052 require firmware v1.2 or higher.

## License

This software is provided as-is for integration with SuperB BMS and Victron systems.

## Support

For issues, check:
1. Service logs: `journalctl -u victron-bms -f`
2. Application log: `/var/log/victron-bms.log`
3. CAN traffic: `candump can0`

## Version History

- **v1.0.0** (2024-12-18): Initial release
  - CANopen SDO client
  - Victron D-Bus integration
  - Auto-discovery
  - Systemd service
  - Configuration file support
