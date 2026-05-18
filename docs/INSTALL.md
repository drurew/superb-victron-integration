# Installation Guide

## Prerequisites

- Victron Cerbo GX (or other Venus OS device) with SSH access
- SuperB Epsilon V2 batteries connected via CAN bus at 250 kbps
- CAN interface configured (typically `vecan0`)
- Root access to the Cerbo GX

## 1. Configure the CAN Interface

The Cerbo GX must have a CAN interface configured at 250 kbps.

### Using the Venus OS GUI

Navigate to Settings > Services > CAN-bus and create a profile for `vecan0`
at 250 kbps with the VE.Can profile.

### Manual Configuration

```bash
# Create CAN profile
echo "250000" > /etc/venus/canbus/vecan0

# Restart CAN services
svc -t /service/vecan-dbus.vecan0
```

## 2. Install Build Tools (one-time)

The Cerbo GX does not include a C compiler by default. Install it once:

```bash
opkg update
opkg install gcc gcc-symlinks binutils libgcc-s-dev
```

These packages total approximately 15 MB of disk space and are only needed
during compilation. They can be removed afterward with
`opkg remove gcc binutils`.

## 3. Build and Install the Driver

```bash
# Clone the repository
cd /data
git clone https://github.com/drurew/superb-victron-integration.git
cd superb-victron-integration

# Build the C driver
make

# Install
cp victron-bms /data/bms/
```

## 4. Configure

Edit the configuration file at `/data/bms/config.ini`:

```ini
[CAN]
interface = vecan0
bitrate = 250000
node_ids = 1,2,3

[Battery]
capacity = 150
chemistry = LiFePO4
number_of_cells = 4
max_charge_current = 150
max_discharge_current = 150
max_charge_voltage = 14.4
```

The `max_charge_current`, `max_discharge_current`, and `max_charge_voltage`
values serve as fallbacks when the BMS does not respond to the corresponding
SDO queries. Under normal operation, the driver publishes the BMS-reported
dynamic limits.

## 5. Start the Driver

### Manual Start

```bash
/data/bms/victron-bms vecan0 &
```

### Automatic Start at Boot (daemontools)

Create a service directory:

```bash
mkdir -p /service/victron-bms/log
```

Create `/service/victron-bms/run`:

```bash
#!/bin/sh
exec 2>&1
exec /data/bms/victron-bms vecan0
```

Create `/service/victron-bms/log/run`:

```bash
#!/bin/sh
exec multilog t s25000 n4 /var/log/victron-bms
```

Make both executable:

```bash
chmod +x /service/victron-bms/run
chmod +x /service/victron-bms/log/run
```

The driver will start automatically within 5 seconds and persist across
reboots.

### Using the Install Script

An automated install script is provided:

```bash
cd /data/superb-victron-integration
./scripts/install-to-cerbo.sh 127.0.0.1
```

## 6. Verify Operation

```bash
# Check process
ps | grep victron-bms

# Check D-Bus values
dbus -y com.victronenergy.battery.canopen_bms_node1 /Soc GetValue
dbus -y com.victronenergy.battery.canopen_bms_node1 /Info/MaxChargeCurrent GetValue
dbus -y com.victronenergy.battery.canopen_bms_node1 /Info/MaxChargeVoltage GetValue

# Monitor CAN traffic
candump vecan0
```

## 7. System Configuration

### Setting the Battery Service (DVCC)

The Victron system needs to know which battery monitor to use for DVCC.
By default, it may select a VE.Direct shunt instead of the CAN BMS.

To set the CAN BMS as the active battery service:

```bash
dbus -y com.victronenergy.settings /Settings/SystemSetup/BatteryService \
  SetValue "com.victronenergy.battery.canopen_bms_node1"
```

Then verify DVCC is enabled:

```bash
dbus -y com.victronenergy.settings /Settings/CGwacs/Dvcc GetValue
```

## Troubleshooting

### No D-Bus services appear

Check that the CAN interface is up:
```bash
ip link show vecan0
```

Verify BMS heartbeats are present:
```bash
candump vecan0 | grep '701\|702\|703'
```

### Charge limits show fallback values (150A, 14.4V)

The BMS did not respond to the SDO queries for charge limits. Verify the
batteries are powered on and in Operational state (candump should show
heartbeats with data byte 0x05 = Operational).

### Driver exits immediately

Run in the foreground to see error messages:
```bash
/data/bms/victron-bms vecan0
```

Common causes:
- CAN interface not configured
- D-Bus system bus not running
- Permission denied (must be run as root)
