#!/usr/bin/env python3
"""
Victron Venus OS D-Bus Multi-Battery Monitor Service
Supports multiple BMS nodes on single CAN bus

Each BMS node appears as a separate battery in Victron system.
"""

import sys
import os
import time
import logging
import configparser
import threading
from typing import Optional, Dict, Any, List

# Setup GLib main loop before importing D-Bus
from gi.repository import GLib
import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop

# Initialize D-Bus main loop FIRST
DBusGMainLoop(set_as_default=True)

# D-Bus connection helper (from Victron community example)
class SystemBus(dbus.bus.BusConnection):
    def __new__(cls):
        return dbus.bus.BusConnection.__new__(cls, dbus.bus.BusConnection.TYPE_SYSTEM)

class SessionBus(dbus.bus.BusConnection):
    def __new__(cls):
        return dbus.bus.BusConnection.__new__(cls, dbus.bus.BusConnection.TYPE_SESSION)

def dbusconnection():
    return SessionBus() if 'DBUS_SESSION_BUS_ADDRESS' in os.environ else SystemBus()

# Add Victron's velib_python to path
try:
    sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python')
    from vedbus import VeDbusService
except ImportError:
    print("WARNING: Running without Victron D-Bus libraries (testing mode)")
    VeDbusService = None

from bms_canopen_client import CANopenSDOClient
from bms_firmware_updater import BMSFirmwareUpdater

logger = logging.getLogger(__name__)


class BatteryMonitor:
    """Single battery monitor instance"""
    
    def __init__(self, node_id: int, device_instance: int, config: configparser.ConfigParser,
                 canopen_client: CANopenSDOClient):
        self.node_id = node_id
        self.device_instance = device_instance
        self.config = config
        self.canopen_client = canopen_client
        self.dbus_service: Optional[VeDbusService] = None
        self.last_update = 0
        self.firmware_updater: Optional[BMSFirmwareUpdater] = None
        
    def setup_dbus(self) -> bool:
        """Initialize D-Bus service for this battery"""
        if VeDbusService is None:
            logger.warning(f"Node {self.node_id}: D-Bus not available (testing mode)")
            return True
        
        service_prefix = self.config['Victron']['service_name_prefix']
        service_name = f"{service_prefix}_node{self.node_id}"
        product_name = self.config['Victron']['product_name']
        
        try:
            # Create separate D-Bus connection for each battery (prevents path conflicts)
            self.dbus_service = VeDbusService(service_name, dbusconnection())
            
            # Product info
            self.dbus_service.add_path('/Mgmt/ProcessName', __file__)
            self.dbus_service.add_path('/Mgmt/ProcessVersion', '1.1.0')
            self.dbus_service.add_path('/Mgmt/Connection', f'CANopen Node {self.node_id}')
            self.dbus_service.add_path('/DeviceInstance', self.device_instance)
            self.dbus_service.add_path('/ProductId', 0)
            self.dbus_service.add_path('/ProductName', f"{product_name} (Node {self.node_id})")
            self.dbus_service.add_path('/FirmwareVersion', '1.0')
            self.dbus_service.add_path('/HardwareVersion', 'Epsilon V2')
            self.dbus_service.add_path('/Connected', 1)
            
            # Custom info
            self.dbus_service.add_path('/CustomName', f'BMS {self.node_id}', writeable=True)
            
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
            
            # Battery details
            capacity = float(self.config['Battery']['capacity'])
            self.dbus_service.add_path('/Capacity', capacity)
            self.dbus_service.add_path('/InstalledCapacity', capacity)
            self.dbus_service.add_path('/ConsumedAmphours', None, writeable=False)
            
            # Battery info
            self.dbus_service.add_path('/Info/BatteryLowVoltage', None, writeable=False)
            self.dbus_service.add_path('/Info/MaxChargeCurrent', None, writeable=False)
            self.dbus_service.add_path('/Info/MaxDischargeCurrent', None, writeable=False)
            
            # System info
            self.dbus_service.add_path('/System/NrOfCellsPerBattery', 
                                      int(self.config['Battery']['number_of_cells']))
            self.dbus_service.add_path('/System/NrOfModulesOnline', 1)
            self.dbus_service.add_path('/System/NrOfModulesOffline', 0)
            self.dbus_service.add_path('/System/NrOfModulesBlockingCharge', 0)
            self.dbus_service.add_path('/System/NrOfModulesBlockingDischarge', 0)
            
            # History
            self.dbus_service.add_path('/History/ChargeCycles', None, writeable=False)
            self.dbus_service.add_path('/History/TotalAhDrawn', None, writeable=False)
            
            # Alarms
            self.dbus_service.add_path('/Alarms/LowVoltage', 0, writeable=False)
            self.dbus_service.add_path('/Alarms/HighVoltage', 0, writeable=False)
            self.dbus_service.add_path('/Alarms/LowCellVoltage', 0, writeable=False)
            self.dbus_service.add_path('/Alarms/HighCellVoltage', 0, writeable=False)
            self.dbus_service.add_path('/Alarms/LowSoc', 0, writeable=False)
            self.dbus_service.add_path('/Alarms/HighChargeCurrent', 0, writeable=False)
            self.dbus_service.add_path('/Alarms/HighDischargeCurrent', 0, writeable=False)
            self.dbus_service.add_path('/Alarms/CellImbalance', 0, writeable=False)
            self.dbus_service.add_path('/Alarms/InternalFailure', 0, writeable=False)
            self.dbus_service.add_path('/Alarms/HighChargeTemperature', 0, writeable=False)
            self.dbus_service.add_path('/Alarms/LowChargeTemperature', 0, writeable=False)
            self.dbus_service.add_path('/Alarms/HighTemperature', 0, writeable=False)
            self.dbus_service.add_path('/Alarms/LowTemperature', 0, writeable=False)
            
            logger.info(f"Node {self.node_id}: D-Bus service initialized ({service_name})")
            return True
            
        except Exception as e:
            logger.error(f"Node {self.node_id}: Failed to setup D-Bus: {e}")
            return False
    
    def update(self) -> bool:
        """Update battery data from BMS"""
        try:
            # Read all parameters
            bms_data = self.canopen_client.read_all_parameters(self.node_id)
            
            if not bms_data:
                logger.warning(f"Node {self.node_id}: No data received")
                return False
            
            # Update D-Bus
            if self.dbus_service:
                self._update_dbus(bms_data)
            
            self.last_update = time.time()
            return True
            
        except Exception as e:
            logger.error(f"Node {self.node_id}: Update error: {e}")
            return False
    
    def _update_dbus(self, bms_data: Dict[str, Any]):
        """Update D-Bus paths with BMS data"""
        try:
            # Essential battery data
            if 'voltage' in bms_data:
                self.dbus_service['/Dc/0/Voltage'] = bms_data['voltage']
            
            if 'current' in bms_data:
                # Current from 0x2000:02 is already signed (positive=charge, negative=discharge)
                current = bms_data['current']
                self.dbus_service['/Dc/0/Current'] = current
                
                if 'voltage' in bms_data:
                    power = bms_data['voltage'] * current
                    self.dbus_service['/Dc/0/Power'] = power
            
            if 'temperature' in bms_data:
                self.dbus_service['/Dc/0/Temperature'] = bms_data['temperature']
            
            if 'soc' in bms_data:
                self.dbus_service['/Soc'] = bms_data['soc']
                
                capacity = float(self.config['Battery']['capacity'])
                consumed = capacity * (100 - bms_data['soc']) / 100
                self.dbus_service['/ConsumedAmphours'] = consumed
            
            if 'cycles' in bms_data:
                self.dbus_service['/History/ChargeCycles'] = int(bms_data['cycles'])
            
            if 'ah_since_eq' in bms_data:
                self.dbus_service['/History/TotalAhDrawn'] = bms_data['ah_since_eq']
            
            logger.debug(f"Node {self.node_id}: V={bms_data.get('voltage', 0):.2f}V, "
                        f"I={bms_data.get('current', 0):.2f}A, "
                        f"SOC={bms_data.get('soc', 0):.0f}%")
            
        except Exception as e:
            logger.error(f"Node {self.node_id}: Error updating D-Bus: {e}")
    
    def update_firmware(self, hex_file_path: str, progress_callback=None) -> bool:
        """
        Update BMS firmware
        
        Args:
            hex_file_path: Path to Intel HEX firmware file
            progress_callback: Optional progress callback
            
        Returns:
            True if update successful, False otherwise
        """
        if not self.firmware_updater:
            logger.error(f"Node {self.node_id}: Firmware updater not initialized")
            return False
        
        return self.firmware_updater.update_firmware(hex_file_path, progress_callback)


class VictronMultiBMSService:
    """Multi-battery Victron BMS Service"""
    
    def __init__(self, config_file: str = '/etc/victron-bms/config.ini'):
        self.config = self.load_config(config_file)
        self.canopen_client: Optional[CANopenSDOClient] = None
        self.batteries: List[BatteryMonitor] = []
        self.running = False
        
    def load_config(self, config_file: str) -> configparser.ConfigParser:
        """Load configuration"""
        config = configparser.ConfigParser()
        
        # Defaults
        config['CAN'] = {
            'interface': 'can0',
            'bitrate': '250000',
            'node_ids': 'auto'  # auto-detect or comma-separated list
        }
        
        config['Victron'] = {
            'service_name_prefix': 'com.victronenergy.battery.canopen_bms',
            'device_instance_start': '1',
            'product_name': 'SuperB Epsilon V2',
            'update_interval': '1.0',
            'mode': 'individual'  # individual or aggregate
        }
        
        config['Battery'] = {
            'capacity': '200',
            'chemistry': 'LiFePO4',
            'number_of_cells': '4'
        }
        
        if os.path.exists(config_file):
            config.read(config_file)
            logger.info(f"Loaded config from {config_file}")
        else:
            logger.warning(f"Config file {config_file} not found, using defaults")
        
        return config
    
    def setup_canopen(self) -> bool:
        """Initialize CANopen client"""
        can_interface = self.config['CAN']['interface']
        bitrate = int(self.config['CAN']['bitrate'])
        
        self.canopen_client = CANopenSDOClient(can_interface, bitrate)
        
        if not self.canopen_client.connect():
            logger.error("Failed to connect to CAN bus")
            return False
        
        # Determine node IDs
        node_ids_config = self.config['CAN']['node_ids'].strip()
        
        if node_ids_config.lower() == 'auto':
            logger.info("Auto-detecting BMS nodes...")
            node_ids = self.canopen_client.scan_network(range(1, 10))
            if not node_ids:
                logger.error("No CANopen nodes found")
                return False
        else:
            # Parse comma-separated list
            node_ids = [int(x.strip()) for x in node_ids_config.split(',')]
        
        logger.info(f"Using BMS nodes: {node_ids}")
        
        # Create battery monitor for each node
        device_instance = int(self.config['Victron']['device_instance_start'])
        
        for node_id in node_ids:
            battery = BatteryMonitor(node_id, device_instance, self.config, self.canopen_client)
            
            # Initialize firmware updater for this node
            battery.firmware_updater = BMSFirmwareUpdater(self.canopen_client.bus, node_id)
            
            if battery.setup_dbus():
                self.batteries.append(battery)
                logger.info(f"Initialized battery monitor for node {node_id} (device instance {device_instance})")
                device_instance += 1
            else:
                logger.error(f"Failed to initialize battery monitor for node {node_id}")
        
        if not self.batteries:
            logger.error("No battery monitors initialized")
            return False
        
        return True
    
    def _update_callback(self):
        """Callback for periodic updates"""
        try:
            for battery in self.batteries:
                battery.update()
            return True  # Continue calling
        except Exception as e:
            logger.error(f"Update error: {e}", exc_info=True)
            return True
    
    def run(self):
        """Main service loop"""
        logger.info(f"Starting Victron Multi-BMS Service ({len(self.batteries)} batteries)")
        
        if not self.setup_canopen():
            logger.error("CANopen setup failed")
            return False
        
        update_interval = int(float(self.config['Victron']['update_interval']) * 1000)  # Convert to ms
        self.running = True
        
        logger.info(f"Service running (update interval: {update_interval}ms)")
        
        # Setup GLib main loop
        mainloop = GLib.MainLoop()
        
        # Register periodic update callback
        GLib.timeout_add(update_interval, self._update_callback)
        
        try:
            mainloop.run()
        except KeyboardInterrupt:
            logger.info("Service interrupted by user")
        except Exception as e:
            logger.error(f"Service error: {e}", exc_info=True)
        finally:
            self.cleanup()
        
        return True
    
    def cleanup(self):
        """Cleanup resources"""
        logger.info("Cleaning up...")
        self.running = False
        
        if self.canopen_client:
            self.canopen_client.disconnect()
        
        for battery in self.batteries:
            if battery.dbus_service:
                battery.dbus_service.__del__()


def main():
    """Main entry point"""
    import argparse
    parser = argparse.ArgumentParser(description='Victron Multi-Battery BMS Monitor')
    parser.add_argument('--interface', default='can0', help='CAN interface name')
    parser.add_argument('--bitrate', type=int, default=250000, help='CAN bitrate')
    parser.add_argument('--log-file', default='/var/log/victron-bms.log', help='Log file path')
    parser.add_argument('config', nargs='?', default='/etc/victron-bms/config.ini', help='Config file path')
    args = parser.parse_args()
    
    # Setup logging with user-specified log file
    log_handlers = [logging.StreamHandler()]
    try:
        log_handlers.append(logging.FileHandler(args.log_file))
    except PermissionError:
        print(f"Warning: Cannot write to {args.log_file}, logging to console only")
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=log_handlers
    )
    
    config_file = args.config
    
    service = VictronMultiBMSService(config_file)
    service.run()


if __name__ == '__main__':
    main()
