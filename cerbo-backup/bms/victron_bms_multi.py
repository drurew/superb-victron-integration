#!/usr/bin/env python3
"""
Victron Venus OS D-Bus Multi-Battery Monitor for SuperB Epsilon V2 BMS.

Queries each BMS node via CANopen SDO and publishes to Victron D-Bus.
Based on reverse-engineering of firmware v1.2.5 and Be In Charge CANOpen.dll.
"""

import sys
import os
import time
import logging
import configparser
import struct
from typing import Optional, Dict, Any

from gi.repository import GLib
import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop

DBusGMainLoop(set_as_default=True)


class SystemBus(dbus.bus.BusConnection):
    def __new__(cls):
        return dbus.bus.BusConnection.__new__(cls, dbus.bus.BusConnection.TYPE_SYSTEM)


class SessionBus(dbus.bus.BusConnection):
    def __new__(cls):
        return dbus.bus.BusConnection.__new__(cls, dbus.bus.BusConnection.TYPE_SESSION)


def dbusconnection():
    return SessionBus() if 'DBUS_SESSION_BUS_ADDRESS' in os.environ else SystemBus()


try:
    sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python')
    from vedbus import VeDbusService
except ImportError:
    print("WARNING: Running without Victron D-Bus libraries (testing mode)")
    VeDbusService = None

import can

logger = logging.getLogger(__name__)

# ─── SDO parameter definitions ───────────────────────────────────────────
# Verified against Be In Charge v1.7.0 CANOpen.dll decompilation
# Format: (index, subindex, data_type, divisor)
SDO_TIMEOUT = 0.150  # 150ms — most objects respond in 3-5ms

SDO_PARAMS = {
    # Battery essentials
    'voltage':       (0x6060, 0x00, 'INT32',  1024.0),
    'current':       (0x2010, 0x00, 'INT32',  1000.0),
    'soc':           (0x6081, 0x00, 'UINT8',  1.0),
    'soh':           (0x2016, 0x01, 'UINT8',  1.0),
    'temperature':   (0x2013, 0x01, 'INT16',  10.0),
    'min_cell_t':    (0x2023, 0x01, 'INT16',  10.0),
    'max_cell_t':    (0x2023, 0x02, 'INT16',  10.0),
    'min_cell_v':    (0x2022, 0x01, 'UINT16', 1.0),
    'max_cell_v':    (0x2022, 0x02, 'UINT16', 1.0),
    'cycles':        (0x2014, 0x00, 'INT16',  1.0),
    'pack_voltage':  (0x2017, 0x01, 'INT32',  1000.0),
    'term_voltage':  (0x2017, 0x02, 'INT32',  1000.0),
    'power':         (0x2016, 0x00, 'INT32',  1000.0),
    'capacity_ah':   (0x2020, 0x00, 'UINT16', 1.0),
    # Charge/discharge limits — correct objects from CANOpen.dll
    # 0x5021:01 = MaxDischargeCurrent (signed negative), 0x5021:02 = MaxChargeCurrent (positive)
    'max_discharge_a':(0x5021, 0x01, 'INT32',  1000.0),
    'max_charge_a':   (0x5021, 0x02, 'INT32',  1000.0),
    # Requested charge voltage: 0x2060:00 UINT32 / 1024
    'max_charge_voltage':(0x2060, 0x00, 'UINT32', 1024.0),
    # Identity (read once, skipped if not needed)
    'vendor_id':     (0x1018, 0x01, 'UINT32', 1.0),
    'product_code':  (0x1018, 0x02, 'UINT32', 1.0),
    'revision':      (0x1018, 0x03, 'UINT32', 1.0),
    'serial':        (0x1018, 0x04, 'UINT32', 1.0),
    # Operational/error state
    'op_state':      (0x2006, 0x00, 'UINT8',  1.0),
    'error_reg':     (0x2004, 0x00, 'UINT16', 1.0),
    'warning_reg':   (0x2005, 0x00, 'UINT16', 1.0),
}

# ─── D-Bus alarm paths ───────────────────────────────────────────────────
ALARM_PATHS = [
    '/Alarms/HighVoltage', '/Alarms/LowVoltage',
    '/Alarms/HighTemperature', '/Alarms/LowTemperature',
    '/Alarms/HighChargeCurrent', '/Alarms/HighDischargeCurrent',
    '/Alarms/HighChargeTemperature', '/Alarms/LowChargeTemperature',
    '/Alarms/CellImbalance', '/Alarms/LowSoc', '/Alarms/InternalFailure',
]


class BatterySDOClient:
    """Reads BMS parameters via CANopen SDO for a single node."""

    def __init__(self, bus: can.Bus, node_id: int):
        self.bus = bus
        self.node_id = node_id
        self.sdo_rx = 0x600 + node_id
        self.sdo_tx = 0x580 + node_id
        self._aborted: set = set()  # skip known-missing objects

    def read_sdo(self, index: int, subindex: int = 0) -> tuple[Optional[int], Optional[int]]:
        """Returns (raw_value, abort_code). One will be None."""
        obj_key = (index, subindex)
        if obj_key in self._aborted:
            return None, 0xDEAD

        data = bytes([0x40, index & 0xFF, (index >> 8) & 0xFF, subindex, 0, 0, 0, 0])
        msg = can.Message(arbitration_id=self.sdo_rx, data=data, is_extended_id=False)
        try:
            self.bus.send(msg)
        except Exception as e:
            logger.error(f"Node {self.node_id} SDO send error: {e}")
            return None, None

        deadline = time.time() + SDO_TIMEOUT
        while time.time() < deadline:
            remaining = max(deadline - time.time(), 0.01)
            try:
                resp = self.bus.recv(timeout=min(0.05, remaining))
                if resp and resp.arbitration_id == self.sdo_tx:
                    cmd = resp.data[0]
                    if cmd == 0x80:
                        abort = struct.unpack('<I', resp.data[4:8])[0]
                        self._aborted.add(obj_key)
                        logger.debug(f"Node {self.node_id} 0x{index:04X}:{subindex}"
                                     f" abort 0x{abort:08X} — blacklisting")
                        return None, abort
                    elif cmd in (0x43, 0x47, 0x4B, 0x4F, 0x41):
                        return struct.unpack('<I', resp.data[4:8])[0], None
            except Exception:
                pass
        return None, None

    def read_parameter(self, param_name: str) -> Optional[float]:
        """Read and scale a named parameter."""
        if param_name not in SDO_PARAMS:
            return None
        idx, sub, dtype, div = SDO_PARAMS[param_name]
        raw_val, abort = self.read_sdo(idx, sub)
        if raw_val is None:
            return None
        # Handle signed types
        if dtype == 'INT32' and raw_val >= 0x80000000:
            raw_val -= 0x100000000
        elif dtype == 'INT16':
            raw_val &= 0xFFFF
            if raw_val >= 0x8000:
                raw_val -= 0x10000
        elif dtype == 'INT8':
            raw_val &= 0xFF
            if raw_val >= 0x80:
                raw_val -= 0x100
        return raw_val / div

    def read_all(self) -> dict[str, Any]:
        """Read all known parameters. Returns dict of name->value."""
        result = {}
        for name in SDO_PARAMS:
            val = self.read_parameter(name)
            if val is not None:
                result[name] = val
        return result


class BatteryMonitor:
    """Monitors one battery via SDO reads and publishes to D-Bus."""

    def __init__(self, sdo_client: BatterySDOClient, device_instance: int,
                 config: configparser.ConfigParser):
        self.sdo_client = sdo_client
        self.node_id = sdo_client.node_id
        self.device_instance = device_instance
        self.config = config
        self.dbus_service: Optional[VeDbusService] = None
        self.last_update = 0
        # Config fallbacks for charge limits
        self.max_charge_current = float(config['Battery'].get('max_charge_current', '150'))
        self.max_discharge_current = float(config['Battery'].get('max_discharge_current', '150'))

    def _read_fw_version(self) -> Optional[str]:
        """Read firmware version string via segmented SDO (startup only)."""
        idx = 0x100A
        msg = can.Message(
            arbitration_id=self.sdo_client.sdo_rx, is_extended_id=False,
            data=[0x40, idx & 0xFF, (idx >> 8) & 0xFF, 0, 0, 0, 0, 0])
        try:
            self.sdo_client.bus.send(msg)
        except Exception:
            return None

        chunks = []
        start = time.time()
        toggle = 0x00
        got_first = False
        while time.time() - start < 2.0:
            resp = self.sdo_client.bus.recv(timeout=0.3)
            if resp is None or resp.arbitration_id != self.sdo_client.sdo_tx:
                continue
            resp_index = resp.data[1] | (resp.data[2] << 8)
            if resp_index != idx and not got_first:
                continue
            cmd = resp.data[0]
            if cmd == 0x80:
                return None
            elif cmd in (0x43, 0x47, 0x4B, 0x4F):
                n = cmd & 0x03
                raw = resp.data[4:8-n]
                null_pos = raw.find(0)
                if null_pos >= 0:
                    return raw[:null_pos].decode('ascii', errors='replace')
                if raw[0] > 0 and raw[0] <= len(raw) - 1:
                    return raw[1:1+raw[0]].decode('ascii', errors='replace')
                return raw.decode('ascii', errors='replace')
            elif cmd == 0x41:
                got_first = True
                chunks.append(resp.data[4:8])
                req = can.Message(
                    arbitration_id=self.sdo_client.sdo_rx, is_extended_id=False,
                    data=[0x60 | toggle, 0, 0, 0, 0, 0, 0, 0])
                self.sdo_client.bus.send(req)
            elif cmd in (0x00, 0x01, 0x10, 0x11, 0x05, 0x15):
                got_first = True
                n = (cmd >> 2) & 0x03
                chunks.append(resp.data[1:7-n+1])
                if cmd & 0x01:
                    break
                toggle ^= 0x10
                req = can.Message(
                    arbitration_id=self.sdo_client.sdo_rx, is_extended_id=False,
                    data=[0x60 | toggle, 0, 0, 0, 0, 0, 0, 0])
                self.sdo_client.bus.send(req)

        if not chunks:
            return None
        all_data = bytearray()
        for chunk in chunks[1:]:
            all_data.extend(chunk)
        null_pos = all_data.find(0)
        if null_pos >= 0:
            return all_data[:null_pos].decode('ascii', errors='replace')
        if len(all_data) > 0 and all_data[0] > 0 and all_data[0] <= len(all_data) - 1:
            return all_data[1:1+all_data[0]].decode('ascii', errors='replace')
        return all_data.decode('ascii', errors='replace') if all_data else None

    def setup_dbus(self) -> bool:
        if VeDbusService is None:
            logger.warning(f"Node {self.node_id}: D-Bus not available")
            return True

        service_prefix = self.config['Victron']['service_name_prefix']
        service_name = f"{service_prefix}_node{self.node_id}"
        product_name = self.config['Victron']['product_name']

        try:
            self.dbus_service = VeDbusService(service_name, dbusconnection())

            self.dbus_service.add_path('/Mgmt/ProcessName', __file__)
            self.dbus_service.add_path('/Mgmt/ProcessVersion', '2.1.0')
            self.dbus_service.add_path('/Mgmt/Connection', f'CANopen SDO Node {self.node_id}')
            self.dbus_service.add_path('/DeviceInstance', self.device_instance)
            self.dbus_service.add_path('/ProductId', 0x000A)
            self.dbus_service.add_path('/ProductName', f"{product_name} (Node {self.node_id})")
            self.dbus_service.add_path('/Connected', 1)
            self.dbus_service.add_path('/CustomName', f'BMS {self.node_id}', writeable=True)
            self.dbus_service.add_path('/HardwareVersion', 'Epsilon V2')

            fw_ver = self._read_fw_version() or 'unknown'
            self.dbus_service.add_path('/FirmwareVersion', fw_ver)

            # Battery essentials
            self.dbus_service.add_path('/Dc/0/Voltage', None, writeable=False,
                                       gettextcallback=lambda p, v: f"{v:.2f}V" if v else "---")
            self.dbus_service.add_path('/Dc/0/Current', None, writeable=False,
                                       gettextcallback=lambda p, v: f"{v:.2f}A" if v else "---")
            self.dbus_service.add_path('/Dc/0/Power', None, writeable=False,
                                       gettextcallback=lambda p, v: f"{v:.0f}W" if v else "---")
            self.dbus_service.add_path('/Dc/0/Temperature', None, writeable=False,
                                       gettextcallback=lambda p, v: f"{v:.1f}°C" if v else "---")
            self.dbus_service.add_path('/Soc', None, writeable=False,
                                       gettextcallback=lambda p, v: f"{v:.0f}%" if v else "---")

            capacity = float(self.config['Battery']['capacity'])
            self.dbus_service.add_path('/Capacity', capacity)
            self.dbus_service.add_path('/InstalledCapacity', capacity)
            self.dbus_service.add_path('/ConsumedAmphours', None, writeable=False)

            self.dbus_service.add_path('/Info/BatteryLowVoltage', None, writeable=False)
            max_chg_v = float(self.config['Battery'].get('max_charge_voltage', '14.4'))
            self.dbus_service.add_path('/Info/MaxChargeVoltage', max_chg_v, writeable=False)
            self.dbus_service.add_path('/Info/MaxChargeCurrent', self.max_charge_current,
                                       writeable=False)
            self.dbus_service.add_path('/Info/MaxDischargeCurrent', self.max_discharge_current,
                                       writeable=False)

            self.dbus_service.add_path('/System/NrOfCellsPerBattery',
                                       int(self.config['Battery'].get('number_of_cells', '4')))
            self.dbus_service.add_path('/System/NrOfModulesOnline', 1)
            self.dbus_service.add_path('/System/NrOfModulesOffline', 0)
            self.dbus_service.add_path('/System/NrOfModulesBlockingCharge', 0)
            self.dbus_service.add_path('/System/NrOfModulesBlockingDischarge', 0)

            self.dbus_service.add_path('/History/ChargeCycles', None, writeable=False)
            self.dbus_service.add_path('/History/TotalAhDrawn', None, writeable=False)

            for path in ALARM_PATHS:
                self.dbus_service.add_path(path, 0, writeable=False)

            logger.info(f"Node {self.node_id}: D-Bus initialized ({service_name})")
            return True
        except Exception as e:
            logger.error(f"Node {self.node_id}: D-Bus setup failed: {e}")
            return False

    def update(self) -> bool:
        """Read SDO data and update D-Bus."""
        if self.dbus_service is None:
            return False
        try:
            params = self.sdo_client.read_all()
            if not params:
                logger.debug(f"Node {self.node_id}: no SDO data")
                return False

            if 'voltage' in params:
                self.dbus_service['/Dc/0/Voltage'] = params['voltage']
            if 'current' in params:
                self.dbus_service['/Dc/0/Current'] = params['current']
                if 'voltage' in params:
                    self.dbus_service['/Dc/0/Power'] = params['voltage'] * params['current']
            if 'soc' in params:
                self.dbus_service['/Soc'] = params['soc']
                capacity = float(self.config['Battery']['capacity'])
                consumed = capacity * (100.0 - params['soc']) / 100.0
                self.dbus_service['/ConsumedAmphours'] = consumed
            if 'temperature' in params:
                self.dbus_service['/Dc/0/Temperature'] = params['temperature']
            if 'cycles' in params:
                self.dbus_service['/History/ChargeCycles'] = int(params['cycles'])
            if 'capacity_ah' in params:
                self.dbus_service['/Capacity'] = params['capacity_ah']
            # Charge limits — abs() because BMS uses signed convention
            if 'max_charge_a' in params:
                self.dbus_service['/Info/MaxChargeCurrent'] = abs(params['max_charge_a'])
            if 'max_discharge_a' in params:
                self.dbus_service['/Info/MaxDischargeCurrent'] = abs(params['max_discharge_a'])
            if 'max_charge_voltage' in params:
                self.dbus_service['/Info/MaxChargeVoltage'] = params['max_charge_voltage']

            self.last_update = time.time()
            self.dbus_service['/Connected'] = 1

            logger.debug(
                f"Node {self.node_id}: V={params.get('voltage', 0):.2f}V, "
                f"I={params.get('current', 0):.2f}A, "
                f"SoC={params.get('soc', 0):.0f}%"
            )
            return True
        except Exception as e:
            logger.error(f"Node {self.node_id}: update error: {e}")
            return False


class VictronMultiBMSService:
    """Multi-battery BMS service — SDO reads to each CANopen node."""

    def __init__(self, config_file: str = '/etc/victron-bms/config.ini'):
        self.config = self.load_config(config_file)
        self.bus: Optional[can.Bus] = None
        self.batteries: dict[int, BatteryMonitor] = {}
        self.running = False

    def load_config(self, config_file: str) -> configparser.ConfigParser:
        config = configparser.ConfigParser()
        config['CAN'] = {'interface': 'vecan0', 'bitrate': '250000',
                         'node_ids': '1,2,3'}
        config['Victron'] = {
            'service_name_prefix': 'com.victronenergy.battery.canopen_bms',
            'device_instance_start': '1',
            'product_name': 'SuperB Epsilon V2',
            'update_interval': '2.0',
        }
        config['Battery'] = {
            'capacity': '150', 'chemistry': 'LiFePO4', 'number_of_cells': '4',
            'max_charge_current': '150', 'max_discharge_current': '150',
            'max_charge_voltage': '14.4',
        }
        if os.path.exists(config_file):
            config.read(config_file)
        return config

    def setup(self) -> bool:
        can_iface = self.config['CAN']['interface']
        bitrate = int(self.config['CAN']['bitrate'])
        self.bus = can.Bus(channel=can_iface, interface='socketcan', bitrate=bitrate)
        logger.info(f"Connected to {can_iface} at {bitrate} bps")

        node_ids_str = self.config['CAN'].get('node_ids', '1,2,3')
        node_ids = [int(x.strip()) for x in node_ids_str.split(',')]
        device_instance = int(self.config['Victron']['device_instance_start'])

        for nid in node_ids:
            sdo = BatterySDOClient(self.bus, nid)
            monitor = BatteryMonitor(sdo, device_instance, self.config)
            if monitor.setup_dbus():
                self.batteries[nid] = monitor
                logger.info(f"Node {nid}: monitor initialized (instance {device_instance})")
                device_instance += 1
            else:
                logger.error(f"Node {nid}: failed to initialize")

        return len(self.batteries) > 0

    def _update_callback(self):
        try:
            now = time.time()
            for battery in self.batteries.values():
                if battery.dbus_service:
                    battery.update()
                    if battery.last_update > 0 and now - battery.last_update > 30:
                        battery.dbus_service['/Connected'] = 0
            return True
        except Exception as e:
            logger.error(f"Update error: {e}", exc_info=True)
            return True

    def run(self):
        logger.info("Starting Victron Multi-BMS Service (SDO-based, 3 nodes)")
        if not self.setup():
            logger.error("Setup failed")
            return

        update_interval = int(float(self.config['Victron']['update_interval']) * 1000)
        self.running = True
        logger.info(f"Service running: {len(self.batteries)} battery(s), interval={update_interval}ms")

        mainloop = GLib.MainLoop()
        GLib.timeout_add(update_interval, self._update_callback)

        try:
            mainloop.run()
        except KeyboardInterrupt:
            logger.info("Interrupted")
        finally:
            self.cleanup()

    def cleanup(self):
        self.running = False
        if self.bus:
            self.bus.shutdown()
        for b in self.batteries.values():
            if b.dbus_service:
                try:
                    b.dbus_service.__del__()
                except Exception:
                    pass


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Victron Multi-Battery BMS Monitor')
    parser.add_argument('--interface', default='vecan0')
    parser.add_argument('--bitrate', type=int, default=250000)
    parser.add_argument('--log-file', default='/var/log/victron-bms.log')
    parser.add_argument('config', nargs='?', default='/etc/victron-bms/config.ini')
    args = parser.parse_args()

    handlers = [logging.StreamHandler()]
    try:
        handlers.append(logging.FileHandler(args.log_file))
    except PermissionError:
        pass
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                        handlers=handlers)

    VictronMultiBMSService(args.config).run()


if __name__ == '__main__':
    main()
