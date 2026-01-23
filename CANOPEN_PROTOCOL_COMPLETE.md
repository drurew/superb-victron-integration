# SuperB Epsilon V2 - Complete CANopen Protocol Documentation

**Version:** 1.0  
**Date:** December 20, 2025  

---

## Table of Contents

1. [Overview](#overview)
2. [CAN Bus Configuration](#can-bus-configuration)
3. [CANopen Object Dictionary](#canopen-object-dictionary)
4. [SDO Protocol](#sdo-protocol)
5. [PDO Configuration](#pdo-configuration)
6. [Bootloader Protocol](#bootloader-protocol)
7. [Verified Objects](#verified-objects)
8. [Data Conversions](#data-conversions)

---

## Overview

### Hardware Platform
- **MCU:** STM32L452RET6 (ARM Cortex-M4, 512KB Flash, 128KB RAM)
- **CANopen Stack:** CANopenNode (open-source implementation)
- **Firmware:** Epsilon2 v1.6.0 (Aug 6 2025)
- **Hardware Revision:** 1.3 (0x04)
- **External Flash:** Spansion S25FL128L (16MB SPI) - stores config/backup

### CANopen Profile
- **CiA 301:** Base CANopen protocol
- **CiA 401:** Generic I/O modules (partial)
- **Custom Profile:** Battery-specific extensions (0x2000-0x6FFF)

### Discovery Methods
1. **Hardware Testing:** Live CAN bus monitoring and UART debugging
2. **Block Upload Protocol:** Bootloader firmware update mechanism

---

## CAN Bus Configuration

### Physical Layer
- **Bitrate:** 250 kbps (standard)
- **Bus Termination:** 120Ω required at both ends
- **Cable:** CAN High/Low twisted pair
- **Connector:** Varies by installation (terminal blocks, DB9, etc.)

### Node Addressing
- **Node ID Range:** 1-127 (configurable)
- **Default Node ID:** Varies by module (typically 1-4 for multi-battery systems)
- **Address Configuration:** Via external flash config or DIP switches (hardware dependent)

### COB-IDs (Communication Object Identifiers)

Based on CANopen DS-301 standard:

| Function | COB-ID Formula | Example (Node 4) | Direction |
|----------|----------------|------------------|-----------|
| NMT | 0x000 | 0x000 | Master → All |
| SYNC | 0x080 | 0x080 | Master → All |
| EMCY | 0x080 + NodeID | 0x084 | BMS → Master |
| TPDO1 | 0x180 + NodeID | 0x184 | BMS → Master |
| RPDO1 | 0x200 + NodeID | 0x204 | Master → BMS |
| TPDO2 | 0x280 + NodeID | 0x284 | BMS → Master |
| RPDO2 | 0x300 + NodeID | 0x304 | Master → BMS |
| TPDO3 | 0x380 + NodeID | 0x384 | BMS → Master |
| RPDO3 | 0x400 + NodeID | 0x404 | Master → BMS |
| TPDO4 | 0x480 + NodeID | 0x484 | BMS → Master |
| RPDO4 | 0x500 + NodeID | 0x504 | Master → BMS |
| SDO TX | 0x580 + NodeID | 0x584 | BMS → Master |
| SDO RX | 0x600 + NodeID | 0x604 | Master → BMS |
| Heartbeat | 0x700 + NodeID | 0x704 | BMS → Master |

### NMT States

| State | Value | Description | CAN Activity |
|-------|-------|-------------|--------------|
| Bootloader | 0x7F | Firmware update mode | Heartbeat only, SDO limited |
| Initialization | 0x00 | Power-on, loading config | Heartbeat, no PDOs |
| Pre-operational | 0x7F | Configuration mode | Heartbeat, SDO only |
| Operational | 0x05 | Normal operation | Heartbeat, SDO, PDOs active |
| Stopped | 0x04 | Halted | Heartbeat only |

---

## CANopen Object Dictionary

### Object Ranges

| Range | Purpose | Access | Description |
|-------|---------|--------|-------------|
| 0x0000 | Reserved | - | Not used |
| 0x0001-0x0FFF | Data Types | RO | CANopen standard data type definitions |
| 0x1000-0x1FFF | Communication | RW/RO | CANopen protocol objects (CiA 301) |
| 0x2000-0x5FFF | Manufacturer | RW/RO | SuperB-specific battery objects |
| 0x6000-0x6FFF | Device Profile | RO | Battery monitoring and control |
| 0x7000-0x7FFF | Reserved | - | Future use |
| 0x8000-0x9FFF | Reserved | - | Future use |
| 0xA000-0xFFFF | Reserved | - | Future use |

### Standard CANopen Objects (0x1000-0x1FFF)

#### Device Information
| Index | Sub | Name | Type | Access | Description |
|-------|-----|------|------|--------|-------------|
| 0x1000 | 0 | Device Type | UNSIGNED32 | RO | Device type identifier |
| 0x1001 | 0 | Error Register | UNSIGNED8 | RO | Error status bits |
| 0x1008 | 0 | Device Name | VISIBLE_STRING | RO | "Epsilon V2" |
| 0x1009 | 0 | Hardware Version | VISIBLE_STRING | RO | "HW 1.3" or similar |
| 0x100A | 0 | Software Version | VISIBLE_STRING | RO | "Epsilon2 1.6.0 / Aug 6 2025" |

**0x1001 Error Register Bits:**
```
Bit 0: Generic error
Bit 1: Current error
Bit 2: Voltage error
Bit 3: Temperature error
Bit 4: Communication error
Bit 5: Device profile specific
Bit 6: Reserved
Bit 7: Manufacturer specific
```

#### Identity Object (0x1018)
| Sub | Name | Type | Value (Example) | Description |
|-----|------|------|-----------------|-------------|
| 0 | Number of Entries | UNSIGNED8 | 4 | Always 4 |
| 1 | Vendor ID | UNSIGNED32 | 0x0000037C | SuperB Technologies |
| 2 | Product Code | UNSIGNED32 | 0x0000000A | Epsilon V2 (10 decimal) |
| 3 | Revision Number | UNSIGNED32 | 0x00010600 | Firmware 1.6.0 |
| 4 | Serial Number | UNSIGNED32 | ********** | Unique per device |

**Verified by:** Bootloader SDO read, DLL line 906-921

#### Communication Control
| Index | Sub | Name | Type | Access | Default | Description |
|-------|-----|------|------|--------|---------|-------------|
| 0x1014 | 0 | COB-ID EMCY | UNSIGNED32 | RW | 0x80+NodeID | Emergency message ID |
| 0x1017 | 0 | Producer Heartbeat | UNSIGNED16 | RW | 1000 | Heartbeat interval (ms) |

#### SDO Parameters (0x1200)
| Sub | Name | Type | Value | Description |
|-----|------|------|-------|-------------|
| 0 | Number of Entries | UNSIGNED8 | 2 | Fixed at 2 |
| 1 | COB-ID Client->Server | UNSIGNED32 | 0x600+NodeID | SDO request |
| 2 | COB-ID Server->Client | UNSIGNED32 | 0x580+NodeID | SDO response |

#### RPDO1 Communication (0x1400)
| Sub | Name | Type | Access | Default | Description |
|-----|------|------|--------|---------|-------------|
| 0 | Highest Sub-index | UNSIGNED8 | RO | 2 | Number of entries |
| 1 | COB-ID | UNSIGNED32 | RW | 0x200+NodeID | RPDO1 identifier |
| 2 | Transmission Type | UNSIGNED8 | RW | 255 | Asynchronous (device triggered) |

**Transmission Type Values:**
- 0: Synchronous (acyclic)
- 1-240: Synchronous (every N SYNC messages)
- 254: Event-driven (manufacturer specific)
- 255: Event-driven (device profile specific)

#### RPDO1 Mapping (0x1600)
| Sub | Name | Type | Access | Description |
|-----|------|------|--------|-------------|
| 0 | Number of Mapped Objects | UNSIGNED8 | RW | 0-8 mapped objects |
| 1-8 | Mapped Object N | UNSIGNED32 | RW | Index:Sub:Size format |

**Mapping Format:** 0xIIIISSLL
- IIII: Index (16 bits)
- SS: Subindex (8 bits)
- LL: Length in bits (8 bits)

Example: 0x60600020 = Index 0x6060, Sub 0, 32 bits (battery voltage)

#### TPDO1 Communication (0x1800)
| Sub | Name | Type | Access | Default | Description |
|-----|------|------|--------|---------|-------------|
| 0 | Highest Sub-index | UNSIGNED8 | RO | 5 | Number of entries |
| 1 | COB-ID | UNSIGNED32 | RW | 0x180+NodeID | TPDO1 identifier |
| 2 | Transmission Type | UNSIGNED8 | RW | 255 | Event-driven |
| 3 | Inhibit Time | UNSIGNED16 | RW | 0 | Minimum interval (×100μs) |
| 5 | Event Timer | UNSIGNED16 | RW | 0 | Periodic transmission (ms), 0=disabled |

#### TPDO1 Mapping (0x1A00)
| Sub | Name | Type | Access | Description |
|-----|------|------|--------|-------------|
| 0 | Number of Mapped Objects | UNSIGNED8 | RW | 0-8 mapped objects |
| 1-8 | Mapped Object N | UNSIGNED32 | RW | Index:Sub:Size format |

---

## SDO Protocol

### SDO Upload (Read from BMS)

**Request (Master → BMS):**
```
COB-ID: 0x600 + NodeID
DLC: 8 bytes
Data[0]: 0x40 (Initiate Upload Request)
Data[1]: Index LSB
Data[2]: Index MSB
Data[3]: Subindex
Data[4-7]: 0x00 (reserved)
```

**Response (BMS → Master):**

**Success - Expedited Transfer (≤4 bytes):**
```
COB-ID: 0x580 + NodeID
Data[0]: 0x4F (1 byte), 0x4B (2 bytes), 0x47 (3 bytes), or 0x43 (4 bytes)
Data[1]: Index LSB
Data[2]: Index MSB
Data[3]: Subindex
Data[4-7]: Data (little-endian)
```

**Success - Segmented Transfer (>4 bytes):**
```
COB-ID: 0x580 + NodeID
Data[0]: 0x41 (Initiate Upload Response)
Data[1]: Index LSB
Data[2]: Index MSB
Data[3]: Subindex
Data[4-7]: Data size (little-endian)
... followed by segment transfers with 0x00/0x10/0x20/0x30 commands
```

**Error - Abort:**
```
COB-ID: 0x580 + NodeID
Data[0]: 0x80 (Abort Transfer)
Data[1]: Index LSB
Data[2]: Index MSB
Data[3]: Subindex
Data[4-7]: Abort code (little-endian)
```

### Common SDO Abort Codes

| Code | Description | Meaning |
|------|-------------|---------|
| 0x05040000 | SDO protocol timeout | No response from device |
| 0x05040001 | Invalid or unknown command | Bad SDO command byte |
| 0x06010000 | Unsupported access | Read/write not allowed |
| 0x06010001 | Write-only object | Cannot read this object |
| 0x06010002 | Read-only object | Cannot write this object |
| 0x06020000 | Object does not exist | Invalid index |
| 0x06040041 | Object cannot be mapped | PDO mapping not supported |
| 0x06040042 | PDO length exceeded | Too many objects in PDO |
| 0x06040043 | General parameter error | Invalid parameter |
| 0x06040047 | General internal error | Device-specific error |
| 0x06060000 | Hardware error | Device malfunction |
| 0x06070010 | Data type mismatch | Wrong data type |
| 0x06070012 | Data cannot be transferred | Transfer failed (bootloader) |
| 0x06070013 | Data cannot be stored | Flash write error |
| 0x06090011 | Sub-index does not exist | Invalid subindex |
| 0x06090030 | Value range exceeded | Data out of bounds |
| 0x06090031 | Value too high | Data exceeds maximum |
| 0x06090032 | Value too low | Data below minimum |
| 0x08000000 | General error | Unspecified error |
| 0x08000020 | Data cannot be written | Write to flash failed |
| 0x08000022 | Flash verification failed | Firmware update error |

### SDO Download (Write to BMS)

**Request (Master → BMS):**
```
COB-ID: 0x600 + NodeID
Data[0]: 0x2F (1 byte), 0x2B (2 bytes), 0x27 (3 bytes), or 0x23 (4 bytes)
Data[1]: Index LSB
Data[2]: Index MSB
Data[3]: Subindex
Data[4-7]: Data (little-endian)
```

**Response (BMS → Master):**
```
COB-ID: 0x580 + NodeID
Data[0]: 0x60 (Download Response)
Data[1]: Index LSB
Data[2]: Index MSB
Data[3]: Subindex
Data[4-7]: 0x00 (reserved)
```

---

## PDO Configuration

### Default PDO Mapping (Typical)

**TPDO1 (0x184)** - Critical Battery Status (100ms)
```
Byte 0-3: Battery Voltage (0x6060:00, INT32, ÷1024 = Volts)
Byte 4: State of Charge (0x6081:00, UINT8, %)
Byte 5-7: Reserved/Padding
```

**TPDO2 (0x284)** - Current and Temperature (100ms)
```
Byte 0-3: Battery Current (0x2010:00, INT32, mA signed)
Byte 4-5: BMS Temperature (0x6010:00, INT16, ÷8 = °C)
Byte 6-7: Reserved/Padding
```

**TPDO3 (0x384)** - Statistics (1000ms)
```
Byte 0-1: Charge Cycles (0x6050:00, UINT16)
Byte 2-5: Ah Since EQ (0x6053:00, INT32, ÷8 = Ah)
Byte 6-7: Reserved/Padding
```

**TPDO4 (0x484)** - Extended Info (1000ms)
```
Byte 0-1: Highest Temp (0x6020:00, INT16, ÷8 = °C)
Byte 2-3: Ah Expended (0x6051:00, INT16, ÷8 = Ah)
Byte 4-5: Ah Returned (0x6052:00, INT16, ÷8 = Ah)
Byte 6-7: Reserved/Padding
```

**Note:** Default PDO mapping may vary by firmware version and configuration. Use SDO reads of 0x1A00-0x1A03 to verify actual mapping.

### Configuring PDOs

**To change TPDO1 mapping:**
1. Disable TPDO1: Write 0x80000184 to 0x1800:01 (set bit 31)
2. Clear mapping: Write 0 to 0x1A00:00
3. Add objects: Write 0xIIIISSLL to 0x1A00:01-08
4. Set count: Write N to 0x1A00:00 (N = number of objects)
5. Enable TPDO1: Write 0x00000184 to 0x1800:01 (clear bit 31)
6. Save configuration: Write 0x65766173 ("save") to 0x1010:01

---

## Bootloader Protocol

### Entering Bootloader Mode

**Method 1: UART Command**
```
arch b
```
⚠️ **WARNING:** Requires physical reset to exit bootloader!

**Method 2: NMT Reset (if supported)**
```
Not verified - may not work on this firmware
```

### Bootloader State

**Heartbeat:** 0x704 = [0x7F] (Pre-operational/Bootloader)

**SDO Access:**
- 0x1018:01-04: Identity (Vendor/Product/Serial) - READ ONLY
- 0x1F50:01: Program Data - WRITE ONLY (block download)
- Other objects may be limited or unavailable

### Block Download Protocol

**SuperB BMS uses CANopen Block Download (NOT Segmented Download)**

**Initiate Block Download:**
```
COB-ID: 0x600 + NodeID
Data[0]: 0xC2 (Initiate Download Request)
Data[1]: 0x50 (0x1F50 LSB)
Data[2]: 0x1F (0x1F50 MSB)
Data[3]: 0x01 (Subindex 1)
Data[4-7]: File size (little-endian, 4 bytes)
```

**Response:**
```
COB-ID: 0x580 + NodeID
Data[0]: 0xA4 (Initiate Block Download Response)
Data[1]: 0x50
Data[2]: 0x1F
Data[3]: 0x01
Data[4]: 0x7F (Block size = 127 segments)
Data[5-7]: Reserved
```

**Block Upload (127 segments):**
```
For sequence 1 to 127:
  COB-ID: 0x600 + NodeID
  Data[0]: Sequence number (1-127)
  Data[1-7]: 7 bytes of firmware data
```

**Block Acknowledge:**
```
COB-ID: 0x580 + NodeID
Data[0]: 0xA2 (Block Download Response)
Data[1]: Acknowledged sequence (last segment received)
Data[2]: Next block size (usually 0x7F for 127)
Data[3-7]: Reserved
```

**End Block Download:**
```
COB-ID: 0x600 + NodeID
Data[0]: 0xC1 (End Block Download)
Data[1]: Last segment byte count (1-7)
Data[2-7]: Reserved/CRC
```

**Upload Statistics:**
- Block size: 127 segments × 7 bytes = 889 bytes per block
- Transfer speed: ~2.7 KB/s at 250kbps with 2ms delays
- Firmware v1.2.5: 341,968 bytes → 385 blocks
- **Failure point:** Block 385 (90%) with error 0x06070012

**Failure Analysis:**
- Bootloader uploads firmware to internal flash (STM32, 512KB)
- At 90%, bootloader attempts to write backup to external flash
- If external flash is empty/damaged, upload fails with 0x06070012
- UART shows: "OD write 1F50, prepare flash..., Flash erase: 1605 ticks"

---

## Verified Objects

### Battery Monitoring Objects (0x6000-0x6FFF)

| Index | Sub | Name | Type | Divisor | Unit | Access | Description |
|-------|-----|------|------|---------|------|--------|-------------|
| 0x6010 | 0 | BMS Temperature | INT16 | 8.0 | °C | RO | Average BMS board temp |
| 0x6020 | 0 | Highest Temp | INT16 | 8.0 | °C | RO | Highest cell temperature |
| 0x6050 | 0 | Charge Cycles | UINT16 | 1.0 | cycles | RO | Total charge cycles |
| 0x6051 | 0 | Ah Expended | INT16 | 8.0 | Ah | RO | Amp-hours discharged |
| 0x6052 | 0 | Ah Returned | INT16 | 8.0 | Ah | RO | Amp-hours charged |
| 0x6053 | 0 | Ah Since EQ | INT32 | 8.0 | Ah | RO | Ah since equalization |
| 0x6060 | 0 | Battery Voltage | INT32 | 1024.0 | V | RO | Pack voltage |
| 0x6081 | 0 | State of Charge | UINT8 | 1.0 | % | RO | SOC (0-100%) |

**Verified by:** DLL lines 1409, 919, decompiled methods, hardware testing

### Manufacturer Objects (0x2000-0x5FFF)

| Index | Sub | Name | Type | Divisor | Unit | Access | Description |
|-------|-----|------|------|---------|------|--------|-------------|
| 0x2010 | 0 | Battery Current | INT32 | 1000.0 | A | RO | Signed: (+)charge, (-)discharge |
| 0x2017 | 1 | Pack Voltage | INT32 | ? | mV | RO | Battery pack voltage |
| 0x2017 | 2 | Terminal Voltage | INT32 | ? | mV | RO | Terminal voltage |

**Note:** Many manufacturer objects (0x2000-0x5FFF) are documented in DLL but not fully tested. See `SDO_VERIFICATION_100_PERCENT.md` for complete list of 226 verified entries.

### Additional Objects from DLL Analysis

The following objects are extracted from CANOpen.dll decompilation (226 total entries). Not all have been hardware tested:

**Configuration Objects:**
- 0x1F80: NMT Startup Configuration
- 0x2001: Manufacturer Errors
- 0x2002: Charger Current
- 0x2003: Manufacturer Status
- 0x2004: Manufacturer Warnings
- 0x2005: Voltage Limits
- 0x2006: Operational State
- 0x200B: Battery Type
- 0x200C-0x200F: Charge cycle statistics

**Cell Data Arrays (likely):**
- 0x5000-0x5FFF range for cell voltages, temperatures, balancing status

**For complete list, see:**
- `SDO_VERIFICATION_100_PERCENT.md` (all 226 entries with DLL line numbers)
- `COMPLETE_SDO_OBJECT_DICTIONARY.md` (organized by function)

---

## Data Conversions

### Voltage
**Battery Voltage (0x6060):**
- Raw: INT32 (signed 32-bit)
- Conversion: `value / 1024.0`
- Example: Raw 55296 = 54.0V
- Range: Typically 40-60V for 48V nominal systems

### Current
**Battery Current (0x2010):**
- Raw: INT32 (signed 32-bit)
- Unit: milliamps
- Conversion: `value / 1000.0`
- Sign: Positive = charging, Negative = discharging
- Example: Raw -15000 = -15.0A (discharging)

### Temperature
**BMS Temperature (0x6010), Highest Temp (0x6020):**
- Raw: INT16 (signed 16-bit)
- Conversion: `value / 8.0`
- Unit: °C
- Example: Raw 200 = 25.0°C
- Range: Typically -20°C to +60°C

### State of Charge
**SOC (0x6081):**
- Raw: UINT8 (unsigned 8-bit)
- Conversion: None (direct percentage)
- Range: 0-100%
- Resolution: 1%

### Capacity
**Ah Expended (0x6051), Ah Returned (0x6052):**
- Raw: INT16 (signed 16-bit)
- Conversion: `value / 8.0`
- Unit: Ah
- Example: Raw 400 = 50.0 Ah

**Ah Since Equalization (0x6053):**
- Raw: INT32 (signed 32-bit)
- Conversion: `value / 8.0`
- Unit: Ah

### Cycles
**Charge Cycles (0x6050):**
- Raw: UINT16 (unsigned 16-bit)
- Conversion: None (direct count)
- Range: 0-65535 cycles

---

## Python Implementation Example

```python
import can
import struct
import time

class SuperBBMS:
    def __init__(self, node_id=4, interface='can0', bitrate=250000):
        self.node_id = node_id
        self.bus = can.Bus(channel=interface, interface='socketcan', bitrate=bitrate)
    
    def read_sdo(self, index, subindex, timeout=0.5):
        """Read SDO value from BMS"""
        # Send SDO upload request
        msg = can.Message(
            arbitration_id=0x600 + self.node_id,
            data=[0x40, index & 0xFF, (index >> 8) & 0xFF, subindex, 0, 0, 0, 0],
            is_extended_id=False
        )
        self.bus.send(msg)
        
        # Wait for response
        start = time.time()
        while time.time() - start < timeout:
            response = self.bus.recv(timeout=timeout - (time.time() - start))
            
            if response and response.arbitration_id == 0x580 + self.node_id:
                if response.data[0] == 0x80:  # Abort
                    abort_code = struct.unpack('<I', response.data[4:8])[0]
                    raise Exception(f"SDO Abort: 0x{abort_code:08X}")
                elif response.data[0] in [0x43, 0x47, 0x4B, 0x4F]:  # Expedited
                    return response.data[4:8]
        
        raise TimeoutError("SDO read timeout")
    
    def get_voltage(self):
        """Read battery voltage"""
        data = self.read_sdo(0x6060, 0x00)
        raw = struct.unpack('<i', data)[0]  # Signed int32
        return raw / 1024.0  # Convert to volts
    
    def get_current(self):
        """Read battery current"""
        data = self.read_sdo(0x2010, 0x00)
        raw = struct.unpack('<i', data)[0]  # Signed int32 (mA)
        return raw / 1000.0  # Convert to amps
    
    def get_soc(self):
        """Read state of charge"""
        data = self.read_sdo(0x6081, 0x00)
        return data[0]  # Direct percentage 0-100
    
    def get_temperature(self):
        """Read BMS temperature"""
        data = self.read_sdo(0x6010, 0x00)
        raw = struct.unpack('<h', data[:2])[0]  # Signed int16
        return raw / 8.0  # Convert to °C
    
    def get_identity(self):
        """Read device identity"""
        vendor = struct.unpack('<I', self.read_sdo(0x1018, 0x01))[0]
        product = struct.unpack('<I', self.read_sdo(0x1018, 0x02))[0]
        revision = struct.unpack('<I', self.read_sdo(0x1018, 0x03))[0]
        serial = struct.unpack('<I', self.read_sdo(0x1018, 0x04))[0]
        
        return {
            'vendor_id': f"0x{vendor:08X}",
            'product_code': f"0x{product:08X}",
            'revision': f"0x{revision:08X}",
            'serial': serial
        }

# Usage
bms = SuperBBMS(node_id=4)
print(f"Voltage: {bms.get_voltage():.2f} V")
print(f"Current: {bms.get_current():.2f} A")
print(f"SOC: {bms.get_soc()}%")
print(f"Temperature: {bms.get_temperature():.1f} °C")
print(f"Identity: {bms.get_identity()}")
```

---

## References

### Source Documents
1. **SDO_VERIFICATION_100_PERCENT.md** - All 226 SDO entries with DLL line numbers
2. **COMPLETE_SDO_OBJECT_DICTIONARY.md** - Organized by function
3. **COMPLETE_SDO_AND_FIRMWARE_ANALYSIS.md** - Comprehensive analysis
4. **SuperB_Epsilon_V2.eds** - CANopen EDS file for this device

### External Standards
- **CiA 301:** CANopen Application Layer and Communication Profile
- **CiA 401:** Generic I/O Modules
- **ISO 11898:** CAN Physical and Data Link Layer

### Hardware Documentation
- **STM32L452:** ARM Cortex-M4 reference manual
- **CANopenNode:** Open-source CANopen stack (github.com/CANopenNode)
- **Spansion S25FL128L:** 16MB SPI Flash datasheet

---

## Revision History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2025-12-20 | Initial comprehensive documentation | Reverse Engineering Team |

---

## Contact & Support

For questions about this documentation or SuperB BMS integration:
- Technical issues: Review source documents and firmware analysis
- Protocol clarifications: Cross-reference with CiA 301 standard
- SuperB official support: Contact SuperB Technologies

**Disclaimer:** This documentation is based on reverse engineering, firmware analysis, and hardware testing. While verified against multiple sources, use at your own risk. For mission-critical applications, consult official SuperB documentation and support.

---

**END OF DOCUMENT**
