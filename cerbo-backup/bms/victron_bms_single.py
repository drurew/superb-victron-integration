#!/usr/bin/env python3
"""
Victron Venus OS D-Bus Single-Battery Monitor Service
One instance per BMS node - run multiple processes for multiple batteries
"""

import sys
import os
import time
import logging
import configparser
import argparse
from typing import Optional

# Setup GLib main loop before importing D-Bus
from gi.repository import GLib
from dbus.mainloop.glib import DBusGMainLoop

# Initialize D-Bus main loop FIRST
DBusGMainLoop(set_as_default=True)

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


class VictronBMSService:
    """Single battery monitor service"""
    
    def __init__(self, config_file: str, node_id: int):
        self.config = self.load_config(config_file)
        self.node_id = node_id
        self.canopen_client: Optional[CANopenSDOClient] = None
        self.dbus_service: Optional[VeDbusService] = None
        self.firmware_updater: Optional[BMSFirmwareUpdater] = None
        
    def load_config(self, config_file: str) -> configparser.ConfigParser:
        """Load configuration from INI file"""
        config = configparser.ConfigParser()
        
        # Defaults
        config['CAN'] = {
            'interface': 'vecan0',
            'bitrate': '250000'
        }
        
        config['Victron'] = {
            'service_name_prefix': 'com.victronenergy.battery.canopen_bms',
            'device_instance_start': '1',
            'product_name': 'SuperB Epsilon V2 BMS',
            'update_interval': '1.0'
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
        """Initialize CANopen connection"""
        interface = self.config['CAN']['interface']
        bitrate = int(self.config['CAN']['bitrate'])
        
        try:
            self.canopen_client = CANopenSDOClient(interface, bitrate)
            self.firmware_updater = BMSFirmwareUpdater(self.canopen_client.bus, self.node_id)
            logger.info(f"Connected to {interface} at {bitrate} bps")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to CAN bus: {e}")
            return False
    
    def setup_dbus(self) -> bool:
        """Initialize D-Bus service"""
        if VeDbusService is None:
            logger.warning("D-Bus not available (testing mode)")
            return True
        
        service_prefix = self.config['Victron']['service_name_prefix']
        service_name = f"{service_prefix}_node{self.node_id}"
        product_name = self.config['Victron']['product_name']
        device_instance = int(self.config['Victron']['device_instance_start']) + (self.node_id - 2)
        
        try:
            self.dbus_service = VeDbusService(service_name)
            
            # Product info
            self.dbus_service.add_path('/Mgmt/ProcessName', __file__)
            self.dbus_service.add_path('/Mgmt/ProcessVersion', '1.2.0')
            self.dbus_service.add_path('/Mgmt/Connection', f'CANopen Node {self.node_id}')
            self.dbus_service.add_path('/DeviceInstance', device_instance)
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
                                      gettextcallback=lambda p, v: f"{v:.1f}%" if v else "---")
            
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
            for alarm in ['LowVoltage', 'HighVoltage', 'LowCellVoltage', 'HighCellVoltage',
                         'LowSoc', 'HighChargeCurrent', 'HighDischargeCurrent', 'CellImbalance',
                         'InternalFailure', 'HighChargeTemperature', 'LowChargeTemperature',
                         'HighTemperature', 'LowTemperature']:
                self.dbus_service.add_path(f'/Alarms/{alarm}', 0, writeable=False)
            
            logger.info(f"D-Bus service initialized ({service_name})")
            return True
            
        except Exception as e:
            logger.error(f"Failed to setup D-Bus: {e}")
            return False
    
    def update_data(self):
        """Update battery data from BMS"""
        try:
            bms_data = self.canopen_client.read_all_parameters(self.node_id)
            
            if not bms_data or not self.dbus_service:
                return True
            
            # Update D-Bus values
            if 'pack_voltage' in bms_data:
                self.dbus_service['/Dc/0/Voltage'] = bms_data['pack_voltage']
            if 'pack_current' in bms_data:
                self.dbus_service['/Dc/0/Current'] = bms_data['pack_current']
            if 'power' in bms_data:
                self.dbus_service['/Dc/0/Power'] = bms_data['power']
            if 'pack_soc' in bms_data:
                self.dbus_service['/Soc'] = bms_data['pack_soc']
            if 'avg_cell_temp' in bms_data:
                self.dbus_service['/Dc/0/Temperature'] = bms_data['avg_cell_temp']
            if 'consumed_ah' in bms_data:
                self.dbus_service['/ConsumedAmphours'] = bms_data['consumed_ah']
            if 'cycle_count' in bms_data:
                self.dbus_service['/History/ChargeCycles'] = bms_data['cycle_count']
            if 'max_charge_current' in bms_data:
                self.dbus_service['/Info/MaxChargeCurrent'] = bms_data['max_charge_current']
            if 'max_discharge_current' in bms_data:
                self.dbus_service['/Info/MaxDischargeCurrent'] = bms_data['max_discharge_current']
            
            return True
        except Exception as e:
            logger.error(f"Update error: {e}")
            return True  # Continue running
    
    def run(self):
        """Main service loop"""
        logger.info(f"Starting Victron BMS Service for Node {self.node_id}")
        
        if not self.setup_canopen():
            logger.error("CANopen setup failed")
            return False
        
        if not self.setup_dbus():
            logger.error("D-Bus setup failed")
            return False
        
        update_interval = int(float(self.config['Victron']['update_interval']) * 1000)
        logger.info(f"Service running (update interval: {update_interval}ms)")
        
        # Setup GLib main loop
        mainloop = GLib.MainLoop()
        GLib.timeout_add(update_interval, self.update_data)
        
        try:
            mainloop.run()
        except KeyboardInterrupt:
            logger.info("Service interrupted by user")
        except Exception as e:
            logger.error(f"Service error: {e}", exc_info=True)
        finally:
            if self.canopen_client:
                self.canopen_client.disconnect()
        
        return True


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description='Victron Single-Battery BMS Monitor')
    parser.add_argument('--node', type=int, required=True, help='BMS node ID (e.g., 2 or 3)')
    parser.add_argument('--config', default='/etc/victron-bms/config.ini', help='Config file path')
    args = parser.parse_args()
    
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler()]
    )
    
    service = VictronBMSService(args.config, args.node)
    service.run()


if __name__ == '__main__':
    main()
