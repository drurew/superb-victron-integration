#!/usr/bin/env python3
"""
Victron Venus OS D-Bus Battery Monitor Service
Integrates SuperB Epsilon V2 BMS via CANopen with Victron Cerbo GX

This service implements the Victron battery monitor D-Bus interface,
allowing the BMS to appear as a native battery in the Victron system.
"""

import sys
import os
import time
import logging
import configparser
from typing import Optional, Dict, Any

# Add Victron's velib_python to path (will be available on Venus OS)
try:
    sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python')
    from vedbus import VeDbusService
except ImportError:
    # For local testing, use dummy implementation
    print("WARNING: Running without Victron D-Bus libraries (testing mode)")
    VeDbusService = None

from bms_canopen_client import CANopenSDOClient

logger = logging.getLogger(__name__)


class VictronBMSService:
    """Victron D-Bus Battery Monitor Service"""
    
    def __init__(self, config_file: str = '/etc/victron-bms/config.ini'):
        """
        Initialize Victron BMS service
        
        Args:
            config_file: Path to configuration file
        """
        self.config = self.load_config(config_file)
        self.canopen_client: Optional[CANopenSDOClient] = None
        self.dbus_service: Optional[VeDbusService] = None
        self.node_id: Optional[int] = None
        self.running = False
        
    def load_config(self, config_file: str) -> configparser.ConfigParser:
        """Load configuration from INI file"""
        config = configparser.ConfigParser()
        
        # Defaults
        config['CAN'] = {
            'interface': 'can0',
            'bitrate': '250000',
            'node_id': '2'  # Default to node 2, will auto-detect if 0
        }
        
        config['Victron'] = {
            'service_name': 'com.victronenergy.battery.canopen_bms',
            'device_instance': '1',
            'product_name': 'SuperB Epsilon V2',
            'update_interval': '1.0'  # seconds
        }
        
        config['Battery'] = {
            'capacity': '200',  # Ah
            'chemistry': 'LiFePO4',
            'number_of_cells': '4'
        }
        
        # Load from file if it exists
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
        
        # Auto-detect node or use configured node
        configured_node = int(self.config['CAN']['node_id'])
        
        if configured_node > 0:
            self.node_id = configured_node
            logger.info(f"Using configured node ID: {self.node_id}")
        else:
            # Auto-detect first available node
            nodes = self.canopen_client.scan_network(range(1, 10))
            if nodes:
                self.node_id = nodes[0]
                logger.info(f"Auto-detected node ID: {self.node_id}")
            else:
                logger.error("No CANopen nodes found")
                return False
        
        return True
    
    def setup_dbus(self) -> bool:
        """Initialize D-Bus service"""
        if VeDbusService is None:
            logger.warning("D-Bus service not available (testing mode)")
            return True
        
        service_name = self.config['Victron']['service_name']
        device_instance = int(self.config['Victron']['device_instance'])
        product_name = self.config['Victron']['product_name']
        
        try:
            self.dbus_service = VeDbusService(service_name)
            
            # Product info
            self.dbus_service.add_path('/Mgmt/ProcessName', __file__)
            self.dbus_service.add_path('/Mgmt/ProcessVersion', '1.0.0')
            self.dbus_service.add_path('/Mgmt/Connection', 'CANopen via USB-CAN')
            self.dbus_service.add_path('/DeviceInstance', device_instance)
            self.dbus_service.add_path('/ProductId', 0)  # 0 = generic
            self.dbus_service.add_path('/ProductName', product_name)
            self.dbus_service.add_path('/FirmwareVersion', '1.0')
            self.dbus_service.add_path('/HardwareVersion', 'Epsilon V2')
            self.dbus_service.add_path('/Connected', 1)
            
            # Battery essentials
            self.dbus_service.add_path('/Dc/0/Voltage', None, writeable=False, gettextcallback=lambda p, v: f"{v:.2f}V")
            self.dbus_service.add_path('/Dc/0/Current', None, writeable=False, gettextcallback=lambda p, v: f"{v:.2f}A")
            self.dbus_service.add_path('/Dc/0/Power', None, writeable=False, gettextcallback=lambda p, v: f"{v:.0f}W")
            self.dbus_service.add_path('/Dc/0/Temperature', None, writeable=False, gettextcallback=lambda p, v: f"{v:.1f}°C")
            self.dbus_service.add_path('/Soc', None, writeable=False, gettextcallback=lambda p, v: f"{v:.0f}%")
            
            # Battery details
            self.dbus_service.add_path('/Capacity', float(self.config['Battery']['capacity']))
            self.dbus_service.add_path('/InstalledCapacity', float(self.config['Battery']['capacity']))
            self.dbus_service.add_path('/ConsumedAmphours', None, writeable=False)
            
            # Battery info
            self.dbus_service.add_path('/Info/BatteryLowVoltage', None, writeable=False)
            self.dbus_service.add_path('/Info/MaxChargeCurrent', None, writeable=False)
            self.dbus_service.add_path('/Info/MaxDischargeCurrent', None, writeable=False)
            
            # System info
            self.dbus_service.add_path('/System/NrOfCellsPerBattery', int(self.config['Battery']['number_of_cells']))
            self.dbus_service.add_path('/System/NrOfModulesOnline', 1)
            self.dbus_service.add_path('/System/NrOfModulesOffline', 0)
            self.dbus_service.add_path('/System/NrOfModulesBlockingCharge', 0)
            self.dbus_service.add_path('/System/NrOfModulesBlockingDischarge', 0)
            
            # History
            self.dbus_service.add_path('/History/ChargeCycles', None, writeable=False)
            self.dbus_service.add_path('/History/TotalAhDrawn', None, writeable=False)
            
            # Alarms (0=OK, 1=Warning, 2=Alarm)
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
            
            logger.info(f"D-Bus service initialized: {service_name}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to setup D-Bus service: {e}")
            return False
    
    def read_bms_data(self) -> Dict[str, Any]:
        """Read current data from BMS"""
        if not self.canopen_client or self.node_id is None:
            return {}
        
        return self.canopen_client.read_all_parameters(self.node_id)
    
    def update_dbus(self, bms_data: Dict[str, Any]):
        """Update D-Bus paths with BMS data"""
        if not self.dbus_service:
            return
        
        try:
            # Essential battery data
            if 'voltage' in bms_data:
                self.dbus_service['/Dc/0/Voltage'] = bms_data['voltage']
            
            if 'current' in bms_data:
                # Note: BMS reports charge current, convert to signed (positive = charging)
                current = bms_data['current']
                self.dbus_service['/Dc/0/Current'] = current
                
                # Calculate power
                if 'voltage' in bms_data:
                    power = bms_data['voltage'] * current
                    self.dbus_service['/Dc/0/Power'] = power
            
            if 'temperature' in bms_data:
                self.dbus_service['/Dc/0/Temperature'] = bms_data['temperature']
            
            if 'soc' in bms_data:
                self.dbus_service['/Soc'] = bms_data['soc']
                
                # Calculate consumed Ah (simple estimation)
                capacity = float(self.config['Battery']['capacity'])
                consumed = capacity * (100 - bms_data['soc']) / 100
                self.dbus_service['/ConsumedAmphours'] = consumed
            
            # History
            if 'cycles' in bms_data:
                self.dbus_service['/History/ChargeCycles'] = int(bms_data['cycles'])
            
            if 'ah_since_eq' in bms_data:
                self.dbus_service['/History/TotalAhDrawn'] = bms_data['ah_since_eq']
            
            # Additional data
            if 'highest_temp' in bms_data and bms_data['highest_temp'] > 0:
                # Could use for high temp alarm logic
                pass
            
            logger.debug(f"Updated D-Bus: V={bms_data.get('voltage', 0):.2f}V, "
                        f"I={bms_data.get('current', 0):.2f}A, "
                        f"SOC={bms_data.get('soc', 0):.0f}%, "
                        f"T={bms_data.get('temperature', 0):.1f}°C")
            
        except Exception as e:
            logger.error(f"Error updating D-Bus: {e}")
    
    def run(self):
        """Main service loop"""
        logger.info("Starting Victron BMS Service")
        
        # Setup
        if not self.setup_canopen():
            logger.error("CANopen setup failed")
            return False
        
        if not self.setup_dbus():
            logger.error("D-Bus setup failed")
            return False
        
        # Main loop
        update_interval = float(self.config['Victron']['update_interval'])
        self.running = True
        
        logger.info(f"Service running (update interval: {update_interval}s)")
        
        try:
            while self.running:
                # Read BMS data
                bms_data = self.read_bms_data()
                
                if bms_data:
                    # Update D-Bus
                    self.update_dbus(bms_data)
                else:
                    logger.warning("No BMS data received")
                
                # Wait for next update
                time.sleep(update_interval)
                
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
        
        if self.dbus_service:
            self.dbus_service.__del__()


def main():
    """Main entry point"""
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('/var/log/victron-bms.log')
        ]
    )
    
    # Create and run service
    config_file = sys.argv[1] if len(sys.argv) > 1 else '/etc/victron-bms/config.ini'
    
    service = VictronBMSService(config_file)
    service.run()


if __name__ == '__main__':
    main()
