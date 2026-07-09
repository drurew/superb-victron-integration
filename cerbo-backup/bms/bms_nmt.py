#!/usr/bin/env python3
"""
CANopen Network Management (NMT) Module
Handles NMT commands for SuperB BMS nodes

NMT Protocol:
- COB-ID: 0x000 (highest priority)
- DLC: 2 bytes [Command Specifier, Node ID]
- Broadcast: Node ID = 0
"""

import can
import logging
import time
from enum import IntEnum
from typing import Optional

logger = logging.getLogger(__name__)


class NMTCommand(IntEnum):
    """NMT Command Specifiers (CS)"""
    START_REMOTE_NODE = 0x01          # Transition to Operational state
    STOP_REMOTE_NODE = 0x02           # Transition to Stopped state
    ENTER_PRE_OPERATIONAL = 0x80      # Transition to Pre-Operational state
    RESET_NODE = 0x81                 # Reset Application
    RESET_COMMUNICATION = 0x82         # Reset Communication only


class NMTState(IntEnum):
    """NMT Node States"""
    INITIALIZING = 0x00
    STOPPED = 0x04
    OPERATIONAL = 0x05
    PRE_OPERATIONAL = 0x7F
    UNKNOWN = 0xFF


class NMTManager:
    """
    CANopen Network Management Manager
    
    Handles NMT commands for controlling BMS node states and reset operations.
    """
    
    NMT_COB_ID = 0x000  # NMT command COB-ID (broadcast)
    
    def __init__(self, bus: can.Bus):
        """
        Initialize NMT Manager
        
        Args:
            bus: python-can Bus instance
        """
        self.bus = bus
        logger.info("NMT Manager initialized")
    
    def send_nmt_command(self, command: NMTCommand, node_id: int = 0) -> bool:
        """
        Send NMT command to a specific node or broadcast
        
        Args:
            command: NMT command to send
            node_id: Target node ID (0 = broadcast to all nodes)
            
        Returns:
            True if command sent successfully
            
        Examples:
            # Reset specific node
            nmt.send_nmt_command(NMTCommand.RESET_NODE, 2)
            
            # Start all nodes
            nmt.send_nmt_command(NMTCommand.START_REMOTE_NODE, 0)
        """
        if not 0 <= node_id <= 127:
            raise ValueError(f"Invalid node ID: {node_id} (must be 0-127)")
        
        try:
            msg = can.Message(
                arbitration_id=self.NMT_COB_ID,
                data=[command, node_id],
                is_extended_id=False
            )
            
            self.bus.send(msg)
            
            target = "all nodes" if node_id == 0 else f"node {node_id}"
            logger.info(f"NMT: {command.name} sent to {target}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send NMT command: {e}")
            return False
    
    def start_node(self, node_id: int = 0) -> bool:
        """
        Start remote node(s) - transition to Operational state
        
        Args:
            node_id: Target node ID (0 = all nodes)
            
        Returns:
            True if successful
        """
        return self.send_nmt_command(NMTCommand.START_REMOTE_NODE, node_id)
    
    def stop_node(self, node_id: int = 0) -> bool:
        """
        Stop remote node(s) - transition to Stopped state
        
        Args:
            node_id: Target node ID (0 = all nodes)
            
        Returns:
            True if successful
        """
        return self.send_nmt_command(NMTCommand.STOP_REMOTE_NODE, node_id)
    
    def enter_pre_operational(self, node_id: int = 0) -> bool:
        """
        Enter Pre-Operational state
        Allows SDO communication but disables PDO
        
        Args:
            node_id: Target node ID (0 = all nodes)
            
        Returns:
            True if successful
        """
        return self.send_nmt_command(NMTCommand.ENTER_PRE_OPERATIONAL, node_id)
    
    def reset_node(self, node_id: int) -> bool:
        """
        Reset node application
        Performs complete device reset (application restart)
        
        Args:
            node_id: Target node ID (cannot be 0/broadcast for safety)
            
        Returns:
            True if successful
        """
        if node_id == 0:
            raise ValueError("Cannot broadcast RESET_NODE command (safety)")
        
        logger.warning(f"Resetting node {node_id} application...")
        return self.send_nmt_command(NMTCommand.RESET_NODE, node_id)
    
    def reset_communication(self, node_id: int) -> bool:
        """
        Reset node communication only
        Resets CAN communication parameters without resetting application
        
        Args:
            node_id: Target node ID (cannot be 0/broadcast for safety)
            
        Returns:
            True if successful
        """
        if node_id == 0:
            raise ValueError("Cannot broadcast RESET_COMMUNICATION command (safety)")
        
        logger.info(f"Resetting node {node_id} communication...")
        return self.send_nmt_command(NMTCommand.RESET_COMMUNICATION, node_id)
    
    def wait_for_bootup(self, node_id: int, timeout: float = 5.0) -> bool:
        """
        Wait for node bootup message after reset
        
        Bootup message: COB-ID = 0x700 + Node-ID, Data = [0x00]
        
        Args:
            node_id: Node ID to wait for
            timeout: Maximum wait time in seconds
            
        Returns:
            True if bootup detected, False if timeout
        """
        bootup_cob_id = 0x700 + node_id
        start_time = time.time()
        
        logger.info(f"Waiting for node {node_id} bootup (COB-ID: 0x{bootup_cob_id:03X})...")
        
        try:
            while time.time() - start_time < timeout:
                msg = self.bus.recv(timeout=0.1)
                if msg and msg.arbitration_id == bootup_cob_id:
                    if len(msg.data) > 0 and msg.data[0] == 0x00:
                        elapsed = time.time() - start_time
                        logger.info(f"Node {node_id} bootup detected after {elapsed:.2f}s")
                        return True
            
            logger.warning(f"Node {node_id} bootup timeout after {timeout}s")
            return False
            
        except Exception as e:
            logger.error(f"Error waiting for bootup: {e}")
            return False
    
    def reset_and_wait(self, node_id: int, timeout: float = 5.0) -> bool:
        """
        Reset node and wait for bootup confirmation
        
        Args:
            node_id: Target node ID
            timeout: Maximum wait time for bootup
            
        Returns:
            True if node reset and booted successfully
        """
        if not self.reset_node(node_id):
            return False
        
        return self.wait_for_bootup(node_id, timeout)


# Convenience functions for direct usage
def create_nmt_manager(interface: str = 'can0', bitrate: int = 250000) -> NMTManager:
    """
    Create NMT manager with CAN interface
    
    Args:
        interface: CAN interface name
        bitrate: CAN bitrate
        
    Returns:
        NMTManager instance
    """
    bus = can.Bus(interface=interface, bustype='socketcan', bitrate=bitrate)
    return NMTManager(bus)


if __name__ == '__main__':
    # Demo/Test code
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    print("=" * 60)
    print("CANopen NMT Manager - Test Mode")
    print("=" * 60)
    
    try:
        # Create NMT manager
        nmt = create_nmt_manager('can0', 250000)
        
        # Test commands
        print("\nAvailable NMT Commands:")
        for cmd in NMTCommand:
            print(f"  {cmd.value:3d} (0x{cmd.value:02X}) - {cmd.name}")
        
        print("\nExample Usage:")
        print("  nmt.start_node(2)           # Start node 2")
        print("  nmt.stop_node(2)            # Stop node 2")
        print("  nmt.reset_node(2)           # Reset node 2")
        print("  nmt.reset_and_wait(2, 5.0)  # Reset and wait for bootup")
        
        # Interactive mode
        print("\n" + "=" * 60)
        print("Interactive Mode - Enter commands:")
        print("  start <node>   - Start node")
        print("  stop <node>    - Stop node")
        print("  reset <node>   - Reset node")
        print("  quit           - Exit")
        print("=" * 60)
        
        while True:
            try:
                cmd = input("\nnmt> ").strip().lower().split()
                if not cmd:
                    continue
                
                if cmd[0] == 'quit':
                    break
                elif cmd[0] == 'start' and len(cmd) == 2:
                    nmt.start_node(int(cmd[1]))
                elif cmd[0] == 'stop' and len(cmd) == 2:
                    nmt.stop_node(int(cmd[1]))
                elif cmd[0] == 'reset' and len(cmd) == 2:
                    node_id = int(cmd[1])
                    nmt.reset_and_wait(node_id, 5.0)
                else:
                    print("Invalid command")
                    
            except KeyboardInterrupt:
                print("\n")
                break
            except Exception as e:
                print(f"Error: {e}")
        
        print("\nExiting NMT Manager")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
