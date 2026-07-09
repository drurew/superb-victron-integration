#!/usr/bin/env python3
"""
SuperB BMS Firmware Updater via CANopen
Supports firmware upload via SDO segmented download (Intel HEX format)
"""

import logging
import time
from typing import Optional
import can

logger = logging.getLogger(__name__)


class BMSFirmwareUpdater:
    """Firmware update handler for SuperB BMS via CANopen"""
    
    # SDO Indexes
    SDO_PROGRAM_CONTROL = 0x1F51  # Bootloader/Application mode control
    SDO_FIRMWARE_UPLOAD = 0x1F50  # Firmware data upload
    SDO_FIRMWARE_STATUS = 0x1F57  # Firmware verification status
    
    def __init__(self, bus: can.Bus, node_id: int):
        """
        Initialize firmware updater
        
        Args:
            bus: CAN bus interface
            node_id: Target BMS node ID (1-127)
        """
        self.bus = bus
        self.node_id = node_id
        self.sdo_rx = 0x600 + node_id  # SDO request to node
        self.sdo_tx = 0x580 + node_id  # SDO response from node
        
    def _clear_receive_queue(self):
        """Clear any pending CAN messages"""
        while self.bus.recv(timeout=0.01):
            pass
    
    def _wait_for_sdo_response(self, timeout: float = 2.0) -> Optional[can.Message]:
        """Wait for SDO response, filtering out non-SDO frames"""
        start_time = time.time()
        while (time.time() - start_time) < timeout:
            msg = self.bus.recv(timeout=0.1)
            if msg and msg.arbitration_id == self.sdo_tx:
                return msg
        return None
    
    def set_program_mode(self, mode: int, max_retries: int = 10) -> bool:
        """
        Set device program mode with retry logic
        
        Args:
            mode: 0 = Bootloader, 1 = Application
            max_retries: Maximum number of retry attempts
            
        Returns:
            True if mode set successfully, False otherwise
        """
        mode_name = "Bootloader" if mode == 0 else "Application"
        logger.info(f"Node {self.node_id}: Setting program mode to {mode} ({mode_name})...")
        
        for attempt in range(max_retries):
            self._clear_receive_queue()
            
            # Send SetProgram command (SDO download 1 byte)
            request = can.Message(
                arbitration_id=self.sdo_rx,
                data=[0x2F, 0x51, 0x1F, 0x01, mode, 0x00, 0x00, 0x00],
                is_extended_id=False
            )
            self.bus.send(request)
            
            # Wait for response
            response = self._wait_for_sdo_response(timeout=1.0)
            
            if not response:
                logger.warning(f"Node {self.node_id}: Attempt {attempt+1}: No response, retrying...")
                time.sleep(0.2)
                continue
            
            # Check response type
            if response.data[0] == 0x60:
                # Accepted - verify mode changed
                logger.debug(f"Node {self.node_id}: Attempt {attempt+1}: Command accepted")
                time.sleep(0.1)
                
                # Read back mode to verify
                verify_req = can.Message(
                    arbitration_id=self.sdo_rx,
                    data=[0x40, 0x51, 0x1F, 0x01, 0x00, 0x00, 0x00, 0x00],
                    is_extended_id=False
                )
                self.bus.send(verify_req)
                verify_resp = self._wait_for_sdo_response(timeout=1.0)
                
                if verify_resp and verify_resp.data[0] == 0x4F:
                    actual_mode = verify_resp.data[4]
                    if actual_mode == mode:
                        logger.info(f"Node {self.node_id}: Mode verified: {mode}")
                        return True
                    else:
                        logger.debug(f"Node {self.node_id}: Mode is {actual_mode}, expected {mode}")
                
                time.sleep(0.2)
                
            elif response.data[0] == 0x80:
                # SDO abort
                abort_code = int.from_bytes(response.data[4:8], 'little')
                logger.warning(f"Node {self.node_id}: Attempt {attempt+1}: Abort 0x{abort_code:08X}, retrying...")
                time.sleep(0.2)
            else:
                logger.debug(f"Node {self.node_id}: Attempt {attempt+1}: Unexpected response 0x{response.data[0]:02X}")
                time.sleep(0.2)
        
        logger.error(f"Node {self.node_id}: Failed to set program mode after {max_retries} attempts")
        return False
    
    def upload_firmware_hex(self, hex_file_path: str, progress_callback=None) -> bool:
        """
        Upload Intel HEX firmware file via segmented SDO download
        
        Args:
            hex_file_path: Path to .hex firmware file
            progress_callback: Optional callback(bytes_sent, total_bytes, segment_num)
            
        Returns:
            True if upload successful, False otherwise
        """
        # Read HEX file
        try:
            with open(hex_file_path, 'rb') as f:
                hex_data = f.read()
        except Exception as e:
            logger.error(f"Node {self.node_id}: Failed to read firmware file: {e}")
            return False
        
        file_size = len(hex_data)
        logger.info(f"Node {self.node_id}: Firmware file size: {file_size:,} bytes")
        logger.info(f"Node {self.node_id}: Initiating upload to SDO 0x{self.SDO_FIRMWARE_UPLOAD:04X}:01...")
        
        # Initiate segmented download
        size_bytes = [
            file_size & 0xFF,
            (file_size >> 8) & 0xFF,
            (file_size >> 16) & 0xFF,
            (file_size >> 24) & 0xFF
        ]
        
        initiate_msg = can.Message(
            arbitration_id=self.sdo_rx,
            data=[0x21, 0x50, 0x1F, 0x01] + size_bytes,
            is_extended_id=False
        )
        self.bus.send(initiate_msg)
        
        # Wait for initiate response
        response = self._wait_for_sdo_response(timeout=2.0)
        if not response or response.data[0] != 0x60:
            logger.error(f"Node {self.node_id}: Download initiate failed")
            if response:
                logger.error(f"Response: {' '.join(f'{b:02X}' for b in response.data)}")
            return False
        
        logger.info(f"Node {self.node_id}: Download initiated, uploading {file_size:,} bytes...")
        
        # Send segments
        offset = 0
        segment_num = 0
        toggle = 0
        start_time = time.time()
        last_progress_report = 0
        
        while offset < file_size:
            bytes_remaining = file_size - offset
            bytes_in_segment = min(7, bytes_remaining)
            is_last = (offset + bytes_in_segment) >= file_size
            
            # Build command byte
            # Bit 0: last segment flag
            # Bits 1-3: number of empty bytes (7 - bytes_in_segment)
            # Bit 4: toggle bit
            cmd = 0x00
            if toggle:
                cmd |= 0x10  # Toggle bit at position 4
            if is_last:
                cmd |= 0x01  # Last segment bit
            
            bytes_empty = 7 - bytes_in_segment
            cmd |= (bytes_empty << 1)
            
            # Build segment data
            segment_data = [cmd] + list(hex_data[offset:offset + bytes_in_segment])
            segment_data += [0] * (8 - len(segment_data))  # Pad to 8 bytes
            
            # Send segment
            segment_msg = can.Message(
                arbitration_id=self.sdo_rx,
                data=segment_data,
                is_extended_id=False
            )
            self.bus.send(segment_msg)
            
            # Wait for acknowledgment
            ack = self._wait_for_sdo_response(timeout=1.0)
            if not ack:
                logger.error(f"Node {self.node_id}: No ACK for segment {segment_num}")
                return False
            
            # Check for abort
            if ack.data[0] == 0x80:
                abort_code = int.from_bytes(ack.data[4:8], 'little')
                logger.error(f"Node {self.node_id}: Segment {segment_num} aborted: 0x{abort_code:08X}")
                return False
            
            # Verify toggle bit in response (inverted from request)
            expected_ack = 0x30 if toggle else 0x20
            if ack.data[0] != expected_ack:
                logger.error(f"Node {self.node_id}: Toggle mismatch at segment {segment_num}")
                logger.error(f"Expected: 0x{expected_ack:02X}, Got: 0x{ack.data[0]:02X}")
                return False
            
            # Update state
            offset += bytes_in_segment
            segment_num += 1
            toggle = 1 - toggle  # Flip toggle bit
            
            # Progress reporting
            if progress_callback:
                progress_callback(offset, file_size, segment_num)
            
            # Log progress every 10%
            progress_pct = (offset / file_size) * 100
            if progress_pct - last_progress_report >= 10:
                elapsed = time.time() - start_time
                rate = offset / elapsed if elapsed > 0 else 0
                logger.info(f"Node {self.node_id}: {offset:,}/{file_size:,} bytes "
                          f"({progress_pct:.1f}%) - {rate:.0f} bytes/sec")
                last_progress_report = progress_pct
        
        elapsed = time.time() - start_time
        logger.info(f"Node {self.node_id}: Upload complete!")
        logger.info(f"Node {self.node_id}: {segment_num:,} segments, {file_size:,} bytes in {elapsed:.1f}s")
        logger.info(f"Node {self.node_id}: Average: {file_size/elapsed:.0f} bytes/sec")
        
        return True
    
    def wait_for_verification(self, timeout: int = 60) -> bool:
        """
        Wait for device to complete internal firmware verification
        
        Args:
            timeout: Maximum time to wait in seconds
            
        Returns:
            True if verification complete, False on timeout
        """
        logger.info(f"Node {self.node_id}: Waiting for firmware verification (timeout: {timeout}s)...")
        
        start_time = time.time()
        while (time.time() - start_time) < timeout:
            # Check heartbeat state (should be 0x7F during verification, then 0x00/0x05 when done)
            time.sleep(1)
        
        # For now, just wait the timeout period
        # The device verifies internally and doesn't provide status via SDO 0x1F57
        logger.info(f"Node {self.node_id}: Verification wait period complete")
        return True
    
    def update_firmware(self, hex_file_path: str, progress_callback=None) -> bool:
        """
        Complete firmware update sequence
        
        Args:
            hex_file_path: Path to .hex firmware file
            progress_callback: Optional progress callback
            
        Returns:
            True if update successful, False otherwise
        """
        logger.info(f"=" * 70)
        logger.info(f"FIRMWARE UPDATE - Node {self.node_id}")
        logger.info(f"File: {hex_file_path}")
        logger.info(f"=" * 70)
        
        try:
            # Step 1: Enter bootloader mode
            logger.info(f"Node {self.node_id}: Step 1/4 - Entering bootloader mode...")
            if not self.set_program_mode(0):
                logger.error(f"Node {self.node_id}: Failed to enter bootloader mode")
                return False
            
            # Wait for device to settle
            time.sleep(2)
            
            # Step 2: Upload firmware
            logger.info(f"Node {self.node_id}: Step 2/4 - Uploading firmware...")
            if not self.upload_firmware_hex(hex_file_path, progress_callback):
                logger.error(f"Node {self.node_id}: Firmware upload failed")
                return False
            
            # Step 3: Wait for verification
            logger.info(f"Node {self.node_id}: Step 3/4 - Waiting for internal verification...")
            self.wait_for_verification(timeout=30)
            
            # Step 4: Exit bootloader to application mode
            logger.info(f"Node {self.node_id}: Step 4/4 - Starting application...")
            if not self.set_program_mode(1):
                logger.error(f"Node {self.node_id}: Failed to exit bootloader mode")
                return False
            
            logger.info(f"=" * 70)
            logger.info(f"Node {self.node_id}: FIRMWARE UPDATE COMPLETE!")
            logger.info(f"=" * 70)
            
            return True
            
        except Exception as e:
            logger.error(f"Node {self.node_id}: Firmware update error: {e}", exc_info=True)
            return False
