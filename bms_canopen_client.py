#!/usr/bin/env python3
"""
CANopen BMS Client for SuperB Epsilon V2 Battery
Reads SDO values from BMS nodes via CAN bus
"""

import can
import struct
import time
import logging
from typing import Optional, Tuple, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SDODefinition:
    """SDO object definition"""
    index: int
    subindex: int
    data_type: str
    divisor: float
    name: str
    unit: str


class CANopenSDOClient:
    """CANopen SDO upload client"""
    
    # Verified SDO definitions from hardware testing
    SDO_MAP = {
        'voltage': SDODefinition(0x6060, 0x00, 'INT32', 1024.0, 'Battery Voltage', 'V'),
        'soc': SDODefinition(0x6081, 0x00, 'UINT8', 1.0, 'State of Charge', '%'),
        'temperature': SDODefinition(0x6010, 0x00, 'INT16', 8.0, 'BMS Temperature', '°C'),
        'current': SDODefinition(0x2010, 0x00, 'INT32', 1000.0, 'Battery Current', 'A'),  # milliamps, signed
        'cycles': SDODefinition(0x6050, 0x00, 'UINT16', 1.0, 'Charge Cycles', ''),
        'ah_since_eq': SDODefinition(0x6053, 0x00, 'INT32', 8.0, 'Ah Since Equalization', 'Ah'),
        'highest_temp': SDODefinition(0x6020, 0x00, 'INT16', 8.0, 'Highest Temperature', '°C'),
        
        # Device identity
        'vendor_id': SDODefinition(0x1018, 0x01, 'UINT32', 1.0, 'Vendor ID', ''),
        'product_code': SDODefinition(0x1018, 0x02, 'UINT32', 1.0, 'Product Code', ''),
        'revision': SDODefinition(0x1018, 0x03, 'UINT32', 1.0, 'Revision', ''),
        'serial': SDODefinition(0x1018, 0x04, 'UINT32', 1.0, 'Serial Number', ''),
        
        # Additional monitoring (firmware version dependent)
        'ah_expended': SDODefinition(0x6051, 0x00, 'INT16', 8.0, 'Ah Expended', 'Ah'),  # v1.2+
        'ah_returned': SDODefinition(0x6052, 0x00, 'INT16', 8.0, 'Ah Returned', 'Ah'),  # v1.2+
    }
    
    def __init__(self, can_interface: str = 'can0', bitrate: int = 250000):
        """
        Initialize CANopen client
        
        Args:
            can_interface: CAN interface name (e.g., 'can0')
            bitrate: CAN bus bitrate (default: 250000)
        """
        self.can_interface = can_interface
        self.bitrate = bitrate
        self.bus: Optional[can.Bus] = None
        
    def connect(self) -> bool:
        """Connect to CAN bus"""
        try:
            # Check if we're on Victron (vecan interface already configured)
            # or if we need to configure the interface ourselves
            if 'vecan' in self.can_interface:
                # Victron interfaces are pre-configured, don't set bitrate
                self.bus = can.Bus(
                    channel=self.can_interface,
                    interface='socketcan'
                )
                logger.info(f"Connected to pre-configured {self.can_interface}")
            else:
                # Regular Linux CAN interface, set bitrate
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
        """Disconnect from CAN bus"""
        if self.bus:
            self.bus.shutdown()
            self.bus = None
            logger.info("Disconnected from CAN bus")
    
    def read_sdo(self, node_id: int, index: int, subindex: int, timeout: float = 0.5) -> Tuple[Optional[bytes], Optional[int]]:
        """
        Read SDO value from CANopen node
        
        Args:
            node_id: CANopen node ID (1-127)
            index: SDO index
            subindex: SDO subindex
            timeout: Response timeout in seconds
            
        Returns:
            Tuple of (data bytes, abort code). One will be None.
        """
        if not self.bus:
            logger.error("CAN bus not connected")
            return None, None
        
        # SDO upload request (0x40 = initiate upload)
        cmd = 0x40
        data = bytes([
            cmd,
            index & 0xFF,
            (index >> 8) & 0xFF,
            subindex,
            0, 0, 0, 0
        ])
        
        # Send to SDO RX (0x600 + node_id)
        msg = can.Message(
            arbitration_id=0x600 + node_id,
            data=data,
            is_extended_id=False
        )
        
        try:
            self.bus.send(msg)
        except Exception as e:
            logger.error(f"Failed to send SDO request: {e}")
            return None, None
        
        # Wait for response from SDO TX (0x580 + node_id)
        start = time.time()
        while time.time() - start < timeout:
            try:
                recv_msg = self.bus.recv(timeout=timeout - (time.time() - start))
                
                if recv_msg and recv_msg.arbitration_id == 0x580 + node_id:
                    # Check response type
                    if recv_msg.data[0] == 0x80:  # Abort
                        abort_code = struct.unpack('<I', recv_msg.data[4:8])[0]
                        logger.debug(f"SDO abort 0x{index:04X}:{subindex:02X} = 0x{abort_code:08X}")
                        return None, abort_code
                    elif recv_msg.data[0] in [0x43, 0x47, 0x4B, 0x4F]:  # Upload response
                        return recv_msg.data[4:8], None
                        
            except Exception as e:
                logger.debug(f"SDO recv error: {e}")
                
        logger.debug(f"SDO timeout 0x{index:04X}:{subindex:02X}")
        return None, None
    
    def decode_value(self, raw_data: bytes, data_type: str) -> Optional[int]:
        """
        Decode raw SDO data based on type
        
        Args:
            raw_data: Raw bytes from SDO response
            data_type: Data type string ('INT16', 'UINT32', etc.)
            
        Returns:
            Decoded integer value or None
        """
        try:
            if data_type == 'UINT8':
                return raw_data[0]
            elif data_type == 'INT8':
                return struct.unpack('<b', raw_data[:1])[0]
            elif data_type == 'UINT16':
                return struct.unpack('<H', raw_data[:2])[0]
            elif data_type == 'INT16':
                return struct.unpack('<h', raw_data[:2])[0]
            elif data_type == 'UINT32':
                return struct.unpack('<I', raw_data[:4])[0]
            elif data_type == 'INT32':
                return struct.unpack('<i', raw_data[:4])[0]
            else:
                logger.error(f"Unknown data type: {data_type}")
                return None
        except Exception as e:
            logger.error(f"Failed to decode {data_type}: {e}")
            return None
    
    def read_parameter(self, node_id: int, param_name: str) -> Optional[float]:
        """
        Read and convert a named parameter
        
        Args:
            node_id: CANopen node ID
            param_name: Parameter name from SDO_MAP
            
        Returns:
            Converted value or None
        """
        if param_name not in self.SDO_MAP:
            logger.error(f"Unknown parameter: {param_name}")
            return None
        
        sdo = self.SDO_MAP[param_name]
        raw_data, abort = self.read_sdo(node_id, sdo.index, sdo.subindex)
        
        if abort is not None:
            logger.debug(f"{param_name} not available (abort 0x{abort:08X})")
            return None
        
        if raw_data is None:
            logger.debug(f"{param_name} timeout")
            return None
        
        raw_value = self.decode_value(raw_data, sdo.data_type)
        if raw_value is None:
            return None
        
        # Apply conversion
        converted = raw_value / sdo.divisor
        logger.info(f"Node {node_id} {param_name}: raw={raw_value}, converted={converted}")
        return converted
    
    def write_sdo(self, node_id: int, index: int, subindex: int, data: bytes, timeout: float = 2.0) -> bool:
        """
        Write SDO value using expedited download
        
        Args:
            node_id: CANopen node ID
            index: SDO index (0x0000-0xFFFF)
            subindex: SDO subindex (0x00-0xFF)
            data: Data bytes to write (1-4 bytes for expedited)
            timeout: Response timeout in seconds
            
        Returns:
            True if successful, False otherwise
        """
        if not self.bus:
            logger.error("CAN bus not connected")
            return False
        
        if len(data) > 4:
            logger.error("Only expedited SDO download (1-4 bytes) supported")
            return False
        
        # Build SDO download request
        # Command byte: 0x2F (1 byte), 0x2B (2 bytes), 0x27 (3 bytes), 0x23 (4 bytes)
        cmd_map = {1: 0x2F, 2: 0x2B, 3: 0x27, 4: 0x23}
        cmd = cmd_map.get(len(data), 0x23)
        
        # Pad data to 4 bytes
        padded_data = data + bytes(4 - len(data))
        
        sdo_tx = 0x600 + node_id
        sdo_rx = 0x580 + node_id
        
        # Create download request message
        msg_data = bytes([
            cmd,
            index & 0xFF,
            (index >> 8) & 0xFF,
            subindex,
            padded_data[0],
            padded_data[1],
            padded_data[2],
            padded_data[3]
        ])
        
        msg = can.Message(
            arbitration_id=sdo_tx,
            data=msg_data,
            is_extended_id=False
        )
        
        try:
            # Send request
            self.bus.send(msg)
            logger.debug(f"SDO write to 0x{index:04X}:{subindex:02X} = {data.hex()}")
            
            # Wait for response
            start_time = time.time()
            
            while (time.time() - start_time) < timeout:
                response = self.bus.recv(timeout=0.1)
                
                if response is None:
                    continue
                
                # Filter by SDO RX ID
                if response.arbitration_id != sdo_rx:
                    continue
                
                # Check for download response (0x60) or abort (0x80)
                if len(response.data) < 1:
                    continue
                
                cmd_byte = response.data[0]
                
                if cmd_byte == 0x60:
                    # Success - verify index/subindex match
                    resp_index = response.data[1] | (response.data[2] << 8)
                    resp_subindex = response.data[3]
                    
                    if resp_index == index and resp_subindex == subindex:
                        logger.debug(f"SDO write successful")
                        return True
                    else:
                        logger.warning(f"Index mismatch in response")
                        continue
                
                elif cmd_byte == 0x80:
                    # Abort response
                    abort_code = int.from_bytes(response.data[4:8], 'little')
                    logger.error(f"SDO write abort: 0x{abort_code:08X}")
                    return False
            
            # Timeout
            logger.error(f"SDO write timeout")
            return False
            
        except Exception as e:
            logger.error(f"SDO write error: {e}")
            return False
    
    def read_all_parameters(self, node_id: int) -> Dict[str, Any]:
        """
        Read all available parameters from a node
        
        Args:
            node_id: CANopen node ID
            
        Returns:
            Dictionary of parameter values
        """
        result = {}
        
        for param_name in self.SDO_MAP.keys():
            value = self.read_parameter(node_id, param_name)
            if value is not None:
                result[param_name] = value
        
        return result
    
    def scan_network(self, node_range: range = range(1, 128)) -> list:
        """
        Scan for active CANopen nodes
        
        Args:
            node_range: Range of node IDs to scan
            
        Returns:
            List of active node IDs
        """
        active_nodes = []
        
        logger.info(f"Scanning for CANopen nodes...")
        
        for node_id in node_range:
            # Try to read device type (0x1000:00)
            raw_data, abort = self.read_sdo(node_id, 0x1000, 0x00, timeout=0.1)
            
            if raw_data is not None:
                device_type = self.decode_value(raw_data, 'UINT32')
                logger.info(f"Found node {node_id}: Device Type = 0x{device_type:08X}")
                active_nodes.append(node_id)
        
        logger.info(f"Scan complete: {len(active_nodes)} node(s) found")
        return active_nodes


if __name__ == '__main__':
    # Test code
    logging.basicConfig(level=logging.DEBUG)
    
    client = CANopenSDOClient('can0')
    if client.connect():
        # Scan for nodes
        nodes = client.scan_network(range(1, 10))
        
        # Read parameters from first node
        if nodes:
            node_id = nodes[0]
            print(f"\n=== Reading parameters from node {node_id} ===")
            params = client.read_all_parameters(node_id)
            
            for name, value in params.items():
                sdo = client.SDO_MAP[name]
                print(f"{sdo.name:30s}: {value:10.3f} {sdo.unit}")
        
        client.disconnect()
