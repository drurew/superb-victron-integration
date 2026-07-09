#!/usr/bin/env python3
"""
Standalone BMS Firmware Update Utility
Updates SuperB BMS firmware via CANopen
"""

import sys
import argparse
import logging
from bms_firmware_updater import BMSFirmwareUpdater
import can

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description='Update SuperB BMS firmware via CANopen',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Update Node 2 with firmware v1.2.5
  %(prog)s --node 2 --file /path/to/Epsilon_V2_Application_v1.2.5.hex
  
  # Update with custom CAN interface
  %(prog)s --node 2 --file firmware.hex --interface can1 --bitrate 500000
  
  # Verbose output
  %(prog)s --node 2 --file firmware.hex --verbose
'''
    )
    
    parser.add_argument('--node', '-n', type=int, required=True,
                       help='BMS node ID (1-127)')
    parser.add_argument('--file', '-f', type=str, required=True,
                       help='Path to Intel HEX firmware file')
    parser.add_argument('--interface', '-i', type=str, default='can0',
                       help='CAN interface name (default: can0)')
    parser.add_argument('--bitrate', '-b', type=int, default=250000,
                       help='CAN bitrate (default: 250000)')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Enable verbose output')
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Initialize CAN bus
    try:
        bus = can.interface.Bus(
            channel=args.interface,
            interface='socketcan',
            bitrate=args.bitrate
        )
    except Exception as e:
        logger.error(f"Failed to initialize CAN bus: {e}")
        return 1
    
    # Create updater
    updater = BMSFirmwareUpdater(bus, args.node)
    
    # Progress callback
    def progress_callback(bytes_sent, total_bytes, segment_num):
        if segment_num % 5000 == 0:
            progress = (bytes_sent / total_bytes) * 100
            print(f"\rProgress: {bytes_sent:,}/{total_bytes:,} bytes ({progress:.1f}%)", 
                  end='', flush=True)
    
    # Perform update
    try:
        success = updater.update_firmware(args.file, progress_callback)
        print()  # New line after progress
        
        if success:
            logger.info("✓ Firmware update completed successfully!")
            return 0
        else:
            logger.error("✗ Firmware update failed")
            return 1
            
    except KeyboardInterrupt:
        logger.warning("\n⚠ Update cancelled by user")
        return 2
    except Exception as e:
        logger.error(f"✗ Unexpected error: {e}", exc_info=True)
        return 3
    finally:
        bus.shutdown()


if __name__ == '__main__':
    sys.exit(main())
