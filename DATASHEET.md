# SuperB Epsilon V2 - Lithium Battery CANopen Protocol

**Version:** 1.0  
**Date:** January 23, 2026  
**Reference:** Based on Firmware v1.2.5 / v1.3.5 Analysis  

---

## 1. Introduction
This document describes the Communication Profile and Object Dictionary for the SuperB Epsilon V2 Lithium-Ion battery system. It is intended for integrators connecting the battery to remote monitoring systems (Victron Cerbo, PLC, etc.) or developing custom drivers.

### 1.1 Scope
The protocol follows the **CiA 301** (CANopen Application Layer) and implements specific manufacturer extensions for battery monitoring.

---

## 2. Physical Layer

### 2.1 Interface Settings
*   **Connector**: Standard CAN High / Low (Terminal Block / DB9)
*   **Bitrate**: **250 kbit/s** (Default)
*   **Termination**: 120Ω required at both ends of the bus.
*   **Galvanic Isolation**: Recommended.

### 2.2 Identification
*   **Vendor ID**: `0x0000037C` (SuperB)
*   **Product Code**: `0x0000000A` (Epsilon V2)

---

## 3. Communication Profile

### 3.1 Pre-defined Connection Set
The node uses standard CANopen Function Codes based on its Node ID (default varies, typically `1` or `10`).

| Object | COB-ID (Hex) | Description |
| :--- | :--- | :--- |
| **NMT** | `0x000` | Network Management (Start/Stop Node) |
| **SYNC** | `0x080` | Synchronization Object |
| **EMCY** | `0x080 + NodeID` | Emergency Error Codes |
| **TPDO1** | `0x180 + NodeID` | Transmit Process Data Object 1 (Status) |
| **TPDO2** | `0x280 + NodeID` | Transmit Process Data Object 2 (Voltages) |
| **TPDO3** | `0x380 + NodeID` | Transmit Process Data Object 3 (Temperatures) |
| **TPDO4** | `0x480 + NodeID` | Transmit Process Data Object 4 (Cell Data) |
| **SDO (TX)**| `0x580 + NodeID` | Server -> Client (Read Response) |
| **SDO (RX)**| `0x600 + NodeID` | Client -> Server (Write/Read Request) |
| **Heartbeat**|`0x700 + NodeID` | Node Guarding / Presence |

---

## 4. Object Dictionary

### 4.1 Communication Objects (0x1000 - 0x1FFF)

| Index | Sub | Name | Type | Access | Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **0x1000** | 00 | Device Type | U32 | RO | Profile Information. |
| **0x1001** | 00 | Error Register | U8 | RO | Bitmask of active internal errors. |
| **0x1017** | 00 | Producer Heartbeat Time | U16 | RW | Heartbeat interval in ms (Default: `1000`). |
| **0x1018** | 00 | Identity Object | REC | RO | Device identification. |
| | 01 | Vendor ID | U32 | RO | `0x0000037C` |
| | 02 | Product Code | U32 | RO | `0x0000000A` |
| | 03 | Revision Number | U32 | RO | Firmware Version (e.g., `0x00010600`). |
| | 04 | Serial Number | U32 | RO | Unique Device Serial. |

### 4.2 Manufacturer Specific Objects (0x2000 - 0x5FFF)
These objects provide high-resolution data and internal BMS status.

| Index | Sub | Name | Type | Unit | Access | Description |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **0x2004** | 00 | **Error Status** | U16 | Bitfield | RO | Active Alarms (See Section 5). |
| **0x2005** | 00 | **Warning Status** | U16 | Bitfield | RO | Pre-alarms / Warnings. |
| **0x2006** | 00 | **Op. State** | U8 | Enum | RO | `0`=Sleep, `1`=Active, `2`=Factory. |
| **0x2010** | 00 | **Current** | I32 | mA | RO | Battery Current (+Chg / -Dischg). |
| **0x2016** | 00 | **Power** | I32 | W | RO | Instantaneous Power. |
| **0x2017** | 01 | **Pack Voltage** | I32 | mV | RO | Total Stack Voltage. |
| | 02 | **Terminal Voltage**| I32 | mV | RO | Voltage at Terminals (after relay). |
| **0x2020** | 00 | **SoC** | I16 | 0.1% | RO | State of Charge (e.g., `500` = 50.0%). |
| **0x2021** | 00 | **SoH** | I16 | 0.1% | RO | State of Health. |
| **0x2022** | 01 | Min Cell Voltage | I16 | mV | RO | Lowest cell in pack. |
| | 02 | Max Cell Voltage | I16 | mV | RO | Highest cell in pack. |
| **0x2023** | 01 | Min Cell Temp | I16 | 0.1°C| RO | Coldest cell. |
| | 02 | Max Cell Temp | I16 | 0.1°C| RO | Hottest cell. |
| **0x2011** | 01-10 | **Cell Voltages** | I16 | mV | RO | Array of 16 Individual Cell Voltages. |
| **0x2012** | 01-10 | **Cell Temps** | I16 | 0.1°C| RO | Array of 16 Individual Cell Temps. |
| **0x2FFD** | 01 | Switch Command | U8 | Enu | WO | `1`=Force ON, `0`=Force OFF. |

### 4.3 Device Profile (0x6000 - 0x9FFF)
Standardized objects mapping to CiA 4xx profiles (Battery).

| Index | Sub | Name | Type | Unit | Access | Description |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **0x6060** | 00 | Battery Voltage | I32 | V/1024 | RO | Voltage in Fixed Point `V * 1024`. |
| **0x6081** | 00 | SoC | U8 | % | RO | Standard 8-bit SoC (0-100%). |
| **0x6050** | 00 | Charge Cycles | U16 | - | RO | Total full cycle count. |
| **0x6052** | 00 | Ah Returned | I16 | Ah | RO | Total charge throughput. |

---

## 5. Bootloader Interface
**Index: 0x1F50**
Used for firmware updates. See `epsilon-firmware-updater` repository for implementation details.

*   **Sub 1**: Program Data (Write Only, Segmented Upload).
*   **Sub 2**: Program Control (`0`=Enter Bootloader, `1`=Jump to App).

---

## 6. Error Codes (0x2004)
Bitmask definitions for the breakdown of the Error Register.

*   **Bit 0**: Cell Over Voltage
*   **Bit 1**: Cell Under Voltage
*   **Bit 2**: Pack Over Voltage
*   **Bit 3**: Pack Under Voltage
*   **Bit 4**: Charge Over Current
*   **Bit 5**: Discharge Over Current
*   **Bit 6**: Cell Over Temp
*   **Bit 7**: Cell Under Temp
*   **Bit 8**: Internal Failure (Relay/Fuse)
*   **Bit 12**: **Configuration Locked** ("Degradation" Mode)
