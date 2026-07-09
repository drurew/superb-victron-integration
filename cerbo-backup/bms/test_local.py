#!/usr/bin/env python3
"""
Local testing script for Victron BMS integration
Tests CANopen communication and data display without D-Bus
"""

import logging
import time
import sys
from bms_canopen_client import CANopenSDOClient

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def print_banner():
    """Print test banner"""
    print("\n" + "="*70)
    print("  Victron BMS CANopen Integration - Local Test")
    print("="*70 + "\n")


def test_canopen_connection(interface='can0', node_id=2):
    """Test CANopen connection and data reading"""
    
    print(f"Connecting to CAN interface: {interface}")
    print(f"Target node ID: {node_id}\n")
    
    # Create client
    client = CANopenSDOClient(interface, bitrate=250000)
    
    # Connect
    if not client.connect():
        logger.error("Failed to connect to CAN bus")
        logger.error("Make sure:")
        logger.error("  1. CAN interface is up: ip link show can0")
        logger.error("  2. CAN bitrate is set: ip link set can0 type can bitrate 250000")
        logger.error("  3. Interface is enabled: ip link set can0 up")
        return False
    
    print("✓ Connected to CAN bus\n")
    
    # Read device identity
    print("-" * 70)
    print("  Device Identity")
    print("-" * 70)
    
    identity = {
        'vendor_id': client.read_parameter(node_id, 'vendor_id'),
        'product_code': client.read_parameter(node_id, 'product_code'),
        'revision': client.read_parameter(node_id, 'revision'),
        'serial': client.read_parameter(node_id, 'serial'),
    }
    
    if identity['vendor_id']:
        print(f"Vendor ID:     0x{int(identity['vendor_id']):08X}")
        print(f"Product Code:  0x{int(identity['product_code']):08X}")
        
        # Decode revision
        rev = int(identity['revision'])
        major = (rev >> 16) & 0xFFFF
        minor = rev & 0xFFFF
        print(f"Firmware:      v{major}.{minor} (0x{rev:08X})")
        
        print(f"Serial Number: {int(identity['serial'])}")
    else:
        print("ERROR: Could not read device identity")
        return False
    
    # Continuous monitoring loop
    print("\n" + "-" * 70)
    print("  Live Battery Data (Press Ctrl+C to stop)")
    print("-" * 70 + "\n")
    
    try:
        while True:
            # Read all parameters
            data = client.read_all_parameters(node_id)
            
            if not data:
                logger.warning("No data received from BMS")
                time.sleep(1)
                continue
            
            # Display in Victron-style format
            voltage = data.get('voltage', 0)
            current = data.get('current', 0)
            soc = data.get('soc', 0)
            temp = data.get('temperature', 0)
            cycles = data.get('cycles', 0)
            
            # Calculate power
            power = voltage * current
            
            # Clear screen and display
            print("\033[H\033[J")  # Clear terminal
            print("=" * 70)
            print(f"  BMS Live Data - Node {node_id}")
            print("=" * 70)
            print()
            print(f"  Battery Voltage:        {voltage:8.2f} V")
            print(f"  Current:                {current:8.2f} A")
            print(f"  Power:                  {power:8.1f} W")
            print(f"  State of Charge:        {soc:8.0f} %")
            print(f"  Temperature:            {temp:8.1f} °C")
            print(f"  Charge Cycles:          {int(cycles):8d}")
            
            if 'highest_temp' in data:
                print(f"  Highest Temperature:    {data['highest_temp']:8.1f} °C")
            
            if 'ah_since_eq' in data:
                print(f"  Ah Since Equalization:  {data['ah_since_eq']:8.2f} Ah")
            
            print()
            print("  Additional Parameters:")
            
            if 'ah_expended' in data:
                print(f"    Ah Expended:          {data['ah_expended']:8.2f} Ah [FW v1.2+]")
            else:
                print(f"    Ah Expended:          N/A (requires FW v1.2+)")
            
            if 'ah_returned' in data:
                print(f"    Ah Returned:          {data['ah_returned']:8.2f} Ah [FW v1.2+]")
            else:
                print(f"    Ah Returned:          N/A (requires FW v1.2+)")
            
            print()
            print("=" * 70)
            print(f"  Last update: {time.strftime('%Y-%m-%d %H:%M:%S')}")
            print("  Press Ctrl+C to stop")
            print("=" * 70)
            
            # Update every second
            time.sleep(1)
    
    except KeyboardInterrupt:
        print("\n\nStopping...")
    
    finally:
        client.disconnect()
        print("Disconnected from CAN bus")
    
    return True


def main():
    """Main test function"""
    print_banner()
    
    # Parse arguments
    interface = sys.argv[1] if len(sys.argv) > 1 else 'can0'
    node_id = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    
    # Run test
    success = test_canopen_connection(interface, node_id)
    
    if success:
        print("\n✓ Test completed successfully")
        return 0
    else:
        print("\n✗ Test failed")
        return 1


if __name__ == '__main__':
    sys.exit(main())
