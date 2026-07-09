#!/usr/bin/env python3
"""
BMS Configuration API - CANopen SDO Write Operations
Handles all configuration and control operations for SuperB BMS
"""

import struct
import logging
import time
from typing import Optional, Any
from bms_canopen_client import CANopenSDOClient

logger = logging.getLogger(__name__)


class BMSConfigurator:
    """Handles BMS configuration via CANopen SDO writes"""
    
    # Known writable SDO indices (from reverse engineering)
    CONFIG_SDO_MAP = {
        # Battery Commands (0x6100 range)
        'find_battery': {'index': 0x6102, 'subindex': 0x00, 'type': 'UINT8', 'desc': 'Find My Battery (seconds)'},
        'clear_event_log': {'index': 0x6103, 'subindex': 0x00, 'type': 'UINT8', 'desc': 'Clear Event Log'},
        
        # Configuration (0x6200 range)
        'battery_capacity': {'index': 0x6220, 'subindex': 0x00, 'type': 'UINT16', 'desc': 'Battery Capacity (Ah)'},
        'soc_shutdown_level': {'index': 0x6210, 'subindex': 0x00, 'type': 'UINT16', 'desc': 'SOC Shutdown Level (%)'},
        
        # Node configuration
        'can_node_id': {'index': 0x2000, 'subindex': 0x00, 'type': 'UINT8', 'desc': 'CAN Node ID'},
        
        # Program Control (Bootloader Mode) - from IProgramControl interface
        'program_mode': {'index': 0x1F57, 'subindex': 0x01, 'type': 'UINT8', 'desc': 'Program Mode (0=Bootloader, 1=Application)'},
    }
    
    # Read-only SDO indices for status
    STATUS_SDO_MAP = {
        'current_program': {'index': 0x1F51, 'subindex': 0x01, 'type': 'UINT8', 'desc': 'Current Program (0=Bootloader, 1=Application)'},
        'firmware_status': {'index': 0x6050, 'subindex': 0x00, 'type': 'UINT32', 'desc': 'Firmware Upload Status'},
    }
    
    def __init__(self, canopen_client: CANopenSDOClient):
        self.client = canopen_client
        
    def write_sdo(self, node_id: int, index: int, subindex: int, 
                  value: int, data_type: str) -> bool:
        """
        Write SDO value to CANopen node
        
        Args:
            node_id: CANopen node ID
            index: SDO index
            subindex: SDO subindex
            value: Integer value to write
            data_type: Data type ('UINT8', 'UINT16', 'UINT32', etc.)
            
        Returns:
            True if successful
        """
        if not self.client.bus:
            logger.error("CAN bus not connected")
            return False
        
        # Encode value based on type
        try:
            if data_type == 'UINT8':
                data_bytes = struct.pack('<B', value)
                n = 3  # 1 byte
            elif data_type == 'INT8':
                data_bytes = struct.pack('<b', value)
                n = 3
            elif data_type == 'UINT16':
                data_bytes = struct.pack('<H', value)
                n = 2  # 2 bytes
            elif data_type == 'INT16':
                data_bytes = struct.pack('<h', value)
                n = 2
            elif data_type == 'UINT32':
                data_bytes = struct.pack('<I', value)
                n = 0  # 4 bytes
            elif data_type == 'INT32':
                data_bytes = struct.pack('<i', value)
                n = 0
            else:
                logger.error(f"Unknown data type: {data_type}")
                return False
        except struct.error as e:
            logger.error(f"Failed to encode value {value} as {data_type}: {e}")
            return False
        
        # SDO download request (expedited)
        cmd = 0x20 | (n << 2) | 0x03  # Expedited transfer, n bytes
        
        # Pad data to 4 bytes
        data_bytes = data_bytes + b'\x00' * (4 - len(data_bytes))
        
        sdo_data = bytes([
            cmd,
            index & 0xFF,
            (index >> 8) & 0xFF,
            subindex
        ]) + data_bytes
        
        # Send SDO write request
        from can import Message
        msg = Message(
            arbitration_id=0x600 + node_id,
            data=sdo_data,
            is_extended_id=False
        )
        
        try:
            self.client.bus.send(msg)
        except Exception as e:
            logger.error(f"Failed to send SDO write: {e}")
            return False
        
        # Wait for response
        start = time.time()
        timeout = 1.0
        
        while time.time() - start < timeout:
            try:
                recv_msg = self.client.bus.recv(timeout=timeout - (time.time() - start))
                
                if recv_msg and recv_msg.arbitration_id == 0x580 + node_id:
                    if recv_msg.data[0] == 0x80:  # Abort
                        abort_code = struct.unpack('<I', recv_msg.data[4:8])[0]
                        logger.error(f"SDO write abort 0x{index:04X}:{subindex:02X} = 0x{abort_code:08X}")
                        return False
                    elif recv_msg.data[0] == 0x60:  # Success
                        logger.info(f"SDO write success 0x{index:04X}:{subindex:02X} = {value}")
                        return True
                        
            except Exception as e:
                logger.debug(f"SDO write recv error: {e}")
        
        logger.error(f"SDO write timeout 0x{index:04X}:{subindex:02X}")
        return False
    
    def find_battery(self, node_id: int, seconds: int = 10) -> bool:
        """
        Trigger 'Find My Battery' LED blink
        
        Args:
            node_id: Node ID
            seconds: Duration in seconds (1-255)
            
        Returns:
            True if successful
        """
        config = self.CONFIG_SDO_MAP['find_battery']
        return self.write_sdo(node_id, config['index'], config['subindex'], 
                             seconds, config['type'])
    
    def clear_event_log(self, node_id: int) -> bool:
        """
        Clear BMS event log
        
        Args:
            node_id: Node ID
            
        Returns:
            True if successful
        """
        config = self.CONFIG_SDO_MAP['clear_event_log']
        return self.write_sdo(node_id, config['index'], config['subindex'], 
                             1, config['type'])
    
    def set_battery_capacity(self, node_id: int, capacity_ah: int) -> bool:
        """
        Set battery capacity
        
        Args:
            node_id: Node ID
            capacity_ah: Capacity in Ah (1-65535)
            
        Returns:
            True if successful
        """
        config = self.CONFIG_SDO_MAP['battery_capacity']
        return self.write_sdo(node_id, config['index'], config['subindex'], 
                             capacity_ah, config['type'])
    
    def set_soc_shutdown_level(self, node_id: int, soc_percent: int) -> bool:
        """
        Set SOC shutdown level
        
        Args:
            node_id: Node ID
            soc_percent: SOC percentage (0-100)
            
        Returns:
            True if successful
        """
        config = self.CONFIG_SDO_MAP['soc_shutdown_level']
        return self.write_sdo(node_id, config['index'], config['subindex'], 
                             soc_percent, config['type'])
    
    def set_can_node_id(self, node_id: int, new_node_id: int) -> bool:
        """
        Change CAN node ID (use with caution!)
        
        Args:
            node_id: Current node ID
            new_node_id: New node ID (1-127)
            
        Returns:
            True if successful
        """
        if not (1 <= new_node_id <= 127):
            logger.error(f"Invalid node ID: {new_node_id} (must be 1-127)")
            return False
        
        config = self.CONFIG_SDO_MAP['can_node_id']
        return self.write_sdo(node_id, config['index'], config['subindex'], 
                             new_node_id, config['type'])
    
    # ============================================================================
    # BOOTLOADER MODE CONTROL
    # ============================================================================
    
    def get_program_mode(self, node_id: int) -> Optional[str]:
        """
        Read current program mode (Application or Bootloader)
        
        Args:
            node_id: Target node ID
            
        Returns:
            'Application', 'Bootloader', or None on error
        """
        try:
            status_config = self.STATUS_SDO_MAP['current_program']
            value = self.client.read_sdo(node_id, status_config['index'], 
                                        status_config['subindex'], status_config['type'])
            
            if value is None:
                return None
            
            # Convert to int if needed (might be string or bytes from SDO read)
            try:
                value = int(value)
            except (ValueError, TypeError):
                logger.error(f"Invalid program mode value: {value} (type: {type(value)})")
                return 'Error'
            
            # 0 = Bootloader, 1 = Application
            return 'Application' if value == 1 else 'Bootloader'
            
        except Exception as e:
            logger.error(f"Failed to read program mode: {e}")
            return None
    
    def enter_bootloader_mode(self, node_id: int) -> bool:
        """
        Switch BMS to bootloader mode
        
        WARNING: This will halt the BMS application!
        Use for firmware updates only.
        
        Args:
            node_id: Target node ID
            
        Returns:
            True if successful
        """
        logger.warning(f"Entering bootloader mode on node {node_id}")
        config = self.CONFIG_SDO_MAP['program_mode']
        return self.write_sdo(node_id, config['index'], config['subindex'], 
                             0, config['type'])  # 0 = Bootloader
    
    def enter_application_mode(self, node_id: int) -> bool:
        """
        Switch BMS to application mode (normal operation)
        
        Args:
            node_id: Target node ID
            
        Returns:
            True if successful
        """
        logger.info(f"Entering application mode on node {node_id}")
        config = self.CONFIG_SDO_MAP['program_mode']
        return self.write_sdo(node_id, config['index'], config['subindex'], 
                             1, config['type'])  # 1 = Application
    
    def get_firmware_status(self, node_id: int) -> Optional[int]:
        """
        Read firmware upload status
        
        Args:
            node_id: Target node ID
            
        Returns:
            Firmware status value (see FirmwareStatus enum) or None
        """
        try:
            status_config = self.STATUS_SDO_MAP['firmware_status']
            value = self.client.read_sdo(node_id, status_config['index'], 
                                        status_config['subindex'], status_config['type'])
            return value
        except Exception as e:
            logger.error(f"Failed to read firmware status: {e}")
            return None
    
    # ============================================================================
    # UTILITY METHODS
    # ============================================================================
    
    def get_writable_parameters(self) -> dict:
        """Get list of all writable parameters"""
        return {
            key: {
                'index': f"0x{val['index']:04X}",
                'subindex': val['subindex'],
                'type': val['type'],
                'description': val['desc']
            }
            for key, val in self.CONFIG_SDO_MAP.items()
        }
    
    def get_status_parameters(self) -> dict:
        """Get list of all status (read-only) parameters"""
        return {
            key: {
                'index': f"0x{val['index']:04X}",
                'subindex': val['subindex'],
                'type': val['type'],
                'description': val['desc']
            }
            for key, val in self.STATUS_SDO_MAP.items()
        }


if __name__ == '__main__':
    # Test code
    logging.basicConfig(level=logging.DEBUG)
    
    client = CANopenSDOClient('can0')
    if client.connect():
        configurator = BMSConfigurator(client)
        
        # Test: Find battery (blink LED for 5 seconds)
        print("Testing 'Find My Battery' command...")
        success = configurator.find_battery(2, 5)
        print(f"Result: {'Success' if success else 'Failed'}")
        
        client.disconnect()
