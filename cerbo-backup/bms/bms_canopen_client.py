#!/usr/bin/env python3
"""
SuperB Epsilon V2 BMS CAN PDO Client
Listens for PDO broadcasts on CAN IDs 0x64-0x68 (100-104 decimal).

Based on reverse-engineering of firmware v1.3.5 (STM32L452, CANopenNode stack).
The BMS broadcasts data as raw PDOs; it does NOT support SDO reads.
"""

import can
import struct
import time
import logging
import threading
from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ─── PDO Format Definitions (confirmed from RE: can_tx_ids_confirmed) ───

# PDO 0x64: Main status — Voltage, Current, SoC, SoH
# bytes[0:1] = Voltage U16 * 0.01V (big-endian)
# bytes[2:3] = Current S16 in Amps (big-endian, positive=charge, negative=discharge)
# bytes[4]   = SoC U8 in %
# bytes[5]   = SoH U8 in %
# bytes[6:7] = reserved

# PDO 0x65: Alarms/status
# byte[0]    = alarm bits: bit0=OVP, bit1=UVP, bit2=OTP, bit3=UTP, bit4=OCC, bit5=ODC, bit6=HCT
# byte[1]    = alarm bits 2: bit5=CellImbalance
# byte[5]    = ModulesOnline U8
# byte[6]    = ModulesOffline U8

# PDO 0x66: Charge limits
# bytes[0:1] = MaxChargeVoltage U16 * 0.1V (big-endian)
# bytes[2:3] = MaxChargeCurrent U16 * 0.1A (big-endian)
# bytes[4:5] = MaxDischargeCurrent U16 * 0.1A (big-endian)
# bytes[6:7] = BatteryLowVoltage U16 * 0.1V (big-endian)

# PDO 0x67: Cell voltages
# bytes[2:3] = MaxCellVoltage U16 * 0.001V (big-endian)
# bytes[4:5] = MinCellVoltage U16 * 0.001V (big-endian)

# PDO 0x68: Temperatures
# byte[5]    = MaxCellTemp S8 in °C
# byte[6]    = MinCellTemp S8 in °C


def _be_u16(data: bytes, offset: int) -> int:
    """Read big-endian uint16 from byte array."""
    return (data[offset] << 8) | data[offset + 1]


def _be_s16(data: bytes, offset: int) -> int:
    """Read big-endian int16 from byte array."""
    val = _be_u16(data, offset)
    if val >= 0x8000:
        val -= 0x10000
    return val


def _be_s8(data: bytes, offset: int) -> int:
    """Read signed int8 from byte array."""
    val = data[offset]
    if val >= 0x80:
        val -= 0x100
    return val


@dataclass
class BMSData:
    """Parsed data from one BMS node."""
    node_id: int = 0
    voltage: Optional[float] = None        # V
    current: Optional[float] = None        # A (positive=charge)
    soc: Optional[float] = None            # %
    soh: Optional[float] = None            # %
    max_charge_voltage: Optional[float] = None   # V
    max_charge_current: Optional[float] = None   # A
    max_discharge_current: Optional[float] = None # A
    battery_low_voltage: Optional[float] = None   # V
    max_cell_voltage: Optional[float] = None      # V
    min_cell_voltage: Optional[float] = None      # V
    max_cell_temp: Optional[float] = None         # °C
    min_cell_temp: Optional[float] = None         # °C
    modules_online: Optional[int] = None
    modules_offline: Optional[int] = None
    alarms: Dict[str, bool] = field(default_factory=dict)
    last_update: float = 0.0
    power: Optional[float] = None          # W (computed)


class BMSCANClient:
    """Listens for PDO broadcasts from SuperB Epsilon V2 BMS on CAN bus."""

    # CAN IDs we listen on — both 11-bit standard and 29-bit extended
    PDO_CAN_IDS = {
        # Standard 11-bit PDOs (RE-confirmed for some BMS variants)
        0x64: "main_status",    # V, I, SoC, SoH
        0x65: "alarms",         # alarm bits, module counts
        0x66: "limits",         # charge/discharge limits
        0x67: "cell_voltage",   # min/max cell V
        0x68: "temperature",    # min/max cell T
        # Extended 29-bit CANopen PDO (observed on working BMS hardware)
        0x1CEFFFE1: "extended_pdo",  # Multiplexed BMS data
    }

    # Alarm bit definitions for PDO 0x65 byte[0]
    ALARM_BITS_BYTE0 = {
        0: "cell_over_voltage",
        1: "cell_under_voltage",
        2: "pack_over_temp",
        3: "pack_under_temp",
        4: "charge_over_current",
        5: "discharge_over_current",
        6: "high_charge_temp",
    }

    # Alarm bits for PDO 0x65 byte[1]
    ALARM_BITS_BYTE1 = {
        5: "cell_imbalance",
    }

    def __init__(self, can_interface: str = 'can0', bitrate: int = 250000):
        self.can_interface = can_interface
        self.bitrate = bitrate
        self.bus: Optional[can.Bus] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Per-node data: keyed by CAN ID offset from 0x64
        # The BMS TX IDs are 0x64-0x68 and do NOT encode node ID.
        # For multi-battery setups, node identification happens differently.
        # By default we assume node_id=0 for the first battery.
        self._data: Dict[int, BMSData] = {}
        self._callbacks: List[Callable[[BMSData], None]] = []
        self._lock = threading.Lock()

    def connect(self) -> bool:
        """Connect to CAN bus."""
        try:
            self.bus = can.Bus(
                channel=self.can_interface,
                interface='socketcan',
                bitrate=self.bitrate
            )
            logger.info(f"Connected to {self.can_interface} at {self.bitrate} bps")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to CAN bus: {e}")
            return False

    def disconnect(self):
        """Disconnect from CAN bus."""
        self.stop()
        if self.bus:
            self.bus.shutdown()
            self.bus = None
            logger.info("Disconnected from CAN bus")

    def add_callback(self, callback: Callable[[BMSData], None]):
        """Register a callback to be called when new BMS data arrives."""
        self._callbacks.append(callback)

    def get_data(self, node_id: int = 0) -> Optional[BMSData]:
        """Get latest data for a node."""
        with self._lock:
            return self._data.get(node_id)

    def get_all_data(self) -> Dict[int, BMSData]:
        """Get all node data."""
        with self._lock:
            return dict(self._data)

    def _parse_pdo_64(self, data: bytes, node_id: int, bms: BMSData):
        """Parse main status PDO (0x64): V, I, SoC, SoH."""
        if len(data) < 6:
            return
        bms.voltage = _be_u16(data, 0) * 0.01
        bms.current = _be_s16(data, 2)  # amps, signed
        bms.soc = float(data[4])         # percent
        bms.soh = float(data[5])         # percent
        if bms.voltage is not None and bms.current is not None:
            bms.power = bms.voltage * bms.current

    def _parse_pdo_65(self, data: bytes, node_id: int, bms: BMSData):
        """Parse alarms PDO (0x65): alarm bits, module counts."""
        if len(data) < 7:
            return
        alarms = {}
        b0 = data[0]
        for bit, name in self.ALARM_BITS_BYTE0.items():
            alarms[name] = bool(b0 & (1 << bit))
        b1 = data[1]
        for bit, name in self.ALARM_BITS_BYTE1.items():
            alarms[name] = bool(b1 & (1 << bit))
        bms.alarms = alarms
        bms.modules_online = data[5]
        bms.modules_offline = data[6]

    def _parse_pdo_66(self, data: bytes, node_id: int, bms: BMSData):
        """Parse limits PDO (0x66): charge/discharge limits."""
        if len(data) < 8:
            return
        bms.max_charge_voltage = _be_u16(data, 0) * 0.1
        bms.max_charge_current = _be_u16(data, 2) * 0.1
        bms.max_discharge_current = _be_u16(data, 4) * 0.1
        bms.battery_low_voltage = _be_u16(data, 6) * 0.1

    def _parse_pdo_67(self, data: bytes, node_id: int, bms: BMSData):
        """Parse cell voltage PDO (0x67): min/max cell V."""
        if len(data) < 6:
            return
        bms.max_cell_voltage = _be_u16(data, 2) * 0.001
        bms.min_cell_voltage = _be_u16(data, 4) * 0.001

    def _parse_pdo_68(self, data: bytes, node_id: int, bms: BMSData):
        """Parse temperature PDO (0x68): min/max cell T."""
        if len(data) < 7:
            return
        bms.max_cell_temp = float(_be_s8(data, 5))
        bms.min_cell_temp = float(_be_s8(data, 6))

    def _parse_extended_pdo(self, data: bytes, node_id: int, bms: BMSData):
        """Parse extended 29-bit CANopen PDO (0x1CEFFFE1, TPDO3 from node 29).
        
        Format (SAM-MPDO):
          bytes[0:1] = fixed header 0x66 0x99
          bytes[2:3] = object index (big-endian U16)
          bytes[4:7] = 32-bit signed value (little-endian I32)
        
        Known indices (verified against hardware):
          0x2002 = pack voltage * 0.01 V   (e.g. 1314 → 13.14V)
          0x0FFF = SoC * 0.01 %            (e.g. 6600 → 66.00%)
          0x201C = current in mA           (e.g. -700 → -0.7A)
          0x200E = modules online / state
          0x2001 = other parameter
        """
        if len(data) < 8:
            return
        
        # Verify header
        if data[0] != 0x66 or data[1] != 0x99:
            return
        
        # Parse index and value (both little-endian, "reversed" byte order)
        obj_idx = struct.unpack('<H', data[2:4])[0]  # little-endian U16
        raw_val = struct.unpack('<i', data[4:8])[0]   # little-endian I32
        
        logger.debug(f"Ext PDO idx=0x{obj_idx:04X} val={raw_val}")
        
        # Known index mappings
        if obj_idx == 0x2002:
            bms.voltage = raw_val * 0.01
        elif obj_idx == 0x0FFF:
            bms.soc = raw_val * 0.01
        elif obj_idx == 0x201C:
            bms.current = raw_val * 0.001  # mA → A
            if bms.voltage is not None:
                bms.power = bms.voltage * bms.current
        elif obj_idx == 0x200E:
            bms.modules_online = raw_val
        elif obj_idx == 0x2001:
            pass  # Unknown parameter, logged above

    _PDO_PARSERS = {
        0x64: _parse_pdo_64,
        0x65: _parse_pdo_65,
        0x66: _parse_pdo_66,
        0x67: _parse_pdo_67,
        0x68: _parse_pdo_68,
        0x1CEFFFE1: _parse_extended_pdo,
    }

    def _handle_message(self, msg: can.Message):
        """Process a single CAN message."""
        can_id = msg.arbitration_id
        if can_id not in self.PDO_CAN_IDS:
            return

        # Determine node ID — for broadcast PDOs on 0x64-0x68, node ID
        # is NOT encoded in the CAN ID. In a multi-battery system on the
        # same CAN bus, each battery must have a unique CAN ID range.
        # BMS firmware CAN IDs are fixed 0x64-0x68 for a single battery.
        node_id = 0

        with self._lock:
            bms = self._data.get(node_id)
            if bms is None:
                bms = BMSData(node_id=node_id)
                self._data[node_id] = bms

        parser = self._PDO_PARSERS.get(can_id)
        if parser:
            parser(self, msg.data, node_id, bms)

        bms.last_update = time.time()

        # Notify callbacks
        for cb in self._callbacks:
            try:
                cb(bms)
            except Exception:
                logger.debug("Callback error", exc_info=True)

    def _listen_loop(self):
        """Background thread: read CAN messages and parse PDOs."""
        logger.info("PDO listener started")
        self._running = True
        while self._running:
            try:
                msg = self.bus.recv(timeout=0.5)
                if msg is not None:
                    self._handle_message(msg)
            except can.CanError as e:
                logger.error(f"CAN error: {e}")
                time.sleep(1)
            except Exception as e:
                logger.error(f"Listener error: {e}", exc_info=True)
        logger.info("PDO listener stopped")

    def start(self):
        """Start listening for PDO broadcasts in a background thread."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("Listener already running")
            return
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the listener thread."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def scan_network(self, timeout: float = 5.0) -> List[int]:
        """
        Discover active BMS nodes by listening for PDO broadcasts.
        Returns list of detected CAN IDs that match BMS PDOs.
        """
        found_ids = set()
        start = time.time()
        logger.info("Scanning for BMS PDO broadcasts...")
        while time.time() - start < timeout:
            try:
                msg = self.bus.recv(timeout=0.5)
                if msg and msg.arbitration_id in self.PDO_CAN_IDS:
                    found_ids.add(msg.arbitration_id)
                    logger.info(f"Found BMS PDO: 0x{msg.arbitration_id:02X}")
            except Exception:
                pass
        # Return node IDs (0-based index derived from first PDO seen)
        result = []
        if found_ids:
            result.append(0)  # Primary battery found
        return result

    # ─── Backward-compatible API (mimics old SDO-based interface) ───

    def read_all_parameters(self, node_id: int = 0) -> Dict[str, Any]:
        """
        Read all available parameters from cached PDO data.
        Compatible with old SDO-based API.
        """
        bms = self.get_data(node_id)
        if bms is None:
            return {}

        result = {}
        if bms.voltage is not None:
            result['voltage'] = bms.voltage
        if bms.current is not None:
            result['current'] = bms.current
        if bms.soc is not None:
            result['soc'] = bms.soc
        if bms.soh is not None:
            result['soh'] = bms.soh
        if bms.max_cell_temp is not None:
            result['temperature'] = bms.max_cell_temp
        if bms.max_cell_voltage is not None:
            result['max_cell_voltage'] = bms.max_cell_voltage
        if bms.min_cell_voltage is not None:
            result['min_cell_voltage'] = bms.min_cell_voltage
        if bms.max_charge_current is not None:
            result['max_charge_current'] = bms.max_charge_current
        if bms.max_discharge_current is not None:
            result['max_discharge_current'] = bms.max_discharge_current
        if bms.max_charge_voltage is not None:
            result['max_charge_voltage'] = bms.max_charge_voltage
        if bms.battery_low_voltage is not None:
            result['battery_low_voltage'] = bms.battery_low_voltage

        return result


# For backward compatibility
CANopenSDOClient = BMSCANClient

if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG,
                       format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    client = BMSCANClient('can0')
    if client.connect():
        print("Scanning for BMS PDO broadcasts...")
        nodes = client.scan_network(timeout=10)

        if nodes:
            print(f"\nFound {len(nodes)} BMS node(s)")
            client.start()
            try:
                time.sleep(5)
                data = client.read_all_parameters(0)
                print(f"\n=== BMS Data ===")
                for name, value in data.items():
                    print(f"  {name:25s}: {value}")
            except KeyboardInterrupt:
                pass

        client.disconnect()
