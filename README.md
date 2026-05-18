# SuperB Epsilon V2 — Victron Cerbo GX Integration

Native CAN bus integration between SuperB Epsilon V2 lithium batteries and
Victron Cerbo GX systems. Publishes per-battery State of Charge, voltage,
current, temperature, and dynamic charge/discharge limits to the Victron
D-Bus, enabling proper DVCC (Distributed Voltage and Current Control)
operation with MultiPlus inverter/chargers and MPPT solar controllers.

## Features

- **Dynamic charge limits** — reads the BMS-requested maximum charge current,
  discharge current, and charge voltage from each battery and publishes them
  to D-Bus so DVCC can safely control charging
- **Multi-battery support** — each battery appears as a separate device on the
  Victron device list with individual SoC, voltage, current, and alarms
- **Minimal resource footprint** — the C driver uses approximately 1 MB of RAM
  and near-zero CPU, compared to approximately 27 MB for the Python reference
  implementation
- **Zero dependencies** — the C driver links against libc only; no Python
  runtime, no CAN libraries, no D-Bus libraries required
- **Firmware safe** — tested against SuperB firmware v1.2.5 (v1.3.5 has a
  known brick mechanism; avoid upgrading without patches)

## Quick Start

```bash
# On the Cerbo GX (SSH as root)
cd /data
git clone https://github.com/drurew/superb-victron-integration.git
cd superb-victron-integration
make
cp victron-bms /data/bms/
/data/bms/victron-bms vecan0 &

# Verify
dbus -y com.victronenergy.battery.canopen_bms_node1 /Soc GetValue
dbus -y com.victronenergy.battery.canopen_bms_node1 /Info/MaxChargeCurrent GetValue
```

## Architecture

```
SuperB Epsilon V2 BMS (Node 1) ──┐
SuperB Epsilon V2 BMS (Node 2) ──┼── CAN bus (vecan0, 250 kbps) ──┐
SuperB Epsilon V2 BMS (Node 3) ──┘                                 │
                                                                    ▼
                                                          victron-bms (C)
                                                          SDO reads each node
                                                                    │
                                                          Victron D-Bus
                                                         ┌────────┼────────┐
                                                         ▼        ▼        ▼
                                                   com.victronenergy.battery
                                                   .canopen_bms_node{1,2,3}
                                                         │
                                                         ▼
                                                   DVCC / SystemCalc
                                                         │
                                        ┌────────────────┼────────────────┐
                                        ▼                ▼                ▼
                                   MultiPlus        MPPT Solar       VRM Portal
```

The driver polls each battery via CANopen SDO (Service Data Object) reads at
2-second intervals. Fast-changing parameters (voltage, current, SoC, charge
limits) are read every cycle. Slower parameters (temperature, cycle count,
cell-level data) are read every 10 cycles to reduce bus load.

## D-Bus Services

| Service | Path | Description |
|---------|------|-------------|
| `canopen_bms_node1` | `/Soc` | State of Charge (0–100%) |
| | `/Dc/0/Voltage` | Battery voltage (V) |
| | `/Dc/0/Current` | Battery current (A) |
| | `/Info/MaxChargeCurrent` | BMS-requested max charge current (A) |
| | `/Info/MaxDischargeCurrent` | BMS-requested max discharge current (A) |
| | `/Info/MaxChargeVoltage` | BMS-requested charge voltage (V) |
| | `/Dc/0/Temperature` | BMS temperature (C) |
| | `/History/ChargeCycles` | Cycle count |

Services `canopen_bms_node2` and `canopen_bms_node3` expose the same paths
for the second and third batteries.

## CAN Protocol

The SuperB Epsilon V2 uses CANopen at 250 kbps with 11-bit identifiers:

| Object | Index | Sub | Type | Description |
|--------|-------|-----|------|-------------|
| Voltage | 0x6060 | 00 | I32/1024 | Battery voltage (V) |
| Current | 0x2010 | 00 | I32/1000 | Battery current (A) |
| SoC | 0x6081 | 00 | U8 | State of charge (%) |
| Max Charge Current | 0x5021 | 02 | I32/1000 | Charge limit (A) |
| Max Discharge Current | 0x5021 | 01 | I32/1000 | Discharge limit (A) |
| Charge Voltage | 0x2060 | 00 | U32/1024 | Requested voltage (V) |
| Temperature | 0x2013 | 01 | I16/10 | BMS temperature (C) |
| Error Register | 0x2004 | 00 | U16 | Active alarms |

Object indices verified against the SuperB Be In Charge CANOpen.dll (v1.7.0).

## Installation

See [docs/INSTALL.md](docs/INSTALL.md) for detailed installation instructions
including automatic startup via daemontools.

## Drivers

- **`src/victron-bms.c`** — Production C driver. Links against libc only.
  Uses raw SocketCAN and raw D-Bus wire protocol. Recommended for all
  installations.
- **`src/victron_bms_multi.py`** — Python reference driver. Uses
  python-can and dbus-python. Useful for development and debugging.

## Requirements

### C Driver
- Linux with SocketCAN support (CONFIG_CAN, CONFIG_CAN_RAW)
- CAN interface configured at 250 kbps (e.g., `vecan0`)
- D-Bus system bus
- GCC or any C99 compiler

### Python Driver
- Python 3.8+
- `python-can`
- `dbus-python`
- `pygobject` (GLib main loop)

## License

MIT — see [LICENSE](LICENSE)
