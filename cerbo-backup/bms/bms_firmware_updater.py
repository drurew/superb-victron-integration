#!/usr/bin/env python3
"""
SuperB Epsilon V2 BMS Firmware Updater via CANopen Block Download.

Protocol based on decompiled Be In Charge v1.7.0 (CANOpen.dll / SuperBBase.cs).
Uses CANopen Block Download on object 0x1F50/0x1F51.

WARNING: Incorrect firmware or interrupted update can brick the BMS permanently.
v1.2.5 firmware is known safe. Avoid v1.3.5 stock (brick risk from ghost_flag_2).
"""

import logging
import struct
import time
import os
from typing import Optional, Callable
import can

logger = logging.getLogger(__name__)


class FirmwareUpdater:
    """CANopen Block Download firmware updater for SuperB Epsilon V2 BMS."""

    # Object dictionary
    OD_PROGRAM_DATA = 0x1F50     # sub 1: firmware data (block download)
    OD_PROGRAM_CTRL = 0x1F51     # sub 1: 0=bootloader, 1=application
    OD_FW_STATUS = 0x1F57        # sub 1: firmware status

    # Block download: 127 segments * 7 bytes = 889 bytes per block
    BLOCK_SIZE = 889
    SEGMENTS_PER_BLOCK = 127
    SEGMENT_DATA_SIZE = 7

    def __init__(self, bus: can.Bus, node_id: int):
        self.bus = bus
        self.node_id = node_id
        self.sdo_rx = 0x600 + node_id
        self.sdo_tx = 0x580 + node_id

    def _sdo_read(self, index: int, sub: int, timeout: float = 1.0
                  ) -> tuple[Optional[int], Optional[int]]:
        """SDO expedited upload. Returns (value, abort_code)."""
        msg = can.Message(
            arbitration_id=self.sdo_rx, is_extended_id=False,
            data=[0x40, index & 0xFF, (index >> 8) & 0xFF, sub, 0, 0, 0, 0]
        )
        self.bus.send(msg)
        t0 = time.time()
        while time.time() - t0 < timeout:
            resp = self.bus.recv(timeout=0.1)
            if resp and resp.arbitration_id == self.sdo_tx:
                cmd = resp.data[0]
                if cmd == 0x80:
                    abort = struct.unpack('<I', resp.data[4:8])[0]
                    return None, abort
                elif cmd in (0x43, 0x47, 0x4B, 0x4F):
                    val = struct.unpack('<I', resp.data[4:8])[0]
                    return val, None
                elif cmd == 0x41:
                    val = struct.unpack('<I', resp.data[4:8])[0]
                    return val, None
        return None, None

    def _sdo_write(self, index: int, sub: int, data: bytes,
                   timeout: float = 2.0) -> bool:
        """SDO expedited download (1-4 bytes). Returns True on success."""
        cmd_map = {1: 0x2F, 2: 0x2B, 3: 0x27, 4: 0x23}
        cmd = cmd_map.get(len(data), 0x23)
        padded = data + bytes(4 - len(data))
        msg = can.Message(
            arbitration_id=self.sdo_rx, is_extended_id=False,
            data=bytes([cmd, index & 0xFF, (index >> 8) & 0xFF, sub,
                        padded[0], padded[1], padded[2], padded[3]])
        )
        self.bus.send(msg)
        t0 = time.time()
        while time.time() - t0 < timeout:
            resp = self.bus.recv(timeout=0.1)
            if resp and resp.arbitration_id == self.sdo_tx:
                if resp.data[0] == 0x60:
                    return True
                elif resp.data[0] == 0x80:
                    abort = struct.unpack('<I', resp.data[4:8])[0]
                    logger.error(f"SDO write abort 0x{abort:08X}")
                    return False
        logger.error("SDO write timeout")
        return False

    # ─── Program mode control ───

    def enter_bootloader(self, retries: int = 5) -> bool:
        """Switch BMS to bootloader mode (0x1F51:01 = 0)."""
        logger.info(f"Node {self.node_id}: Entering bootloader mode...")
        for attempt in range(retries):
            if self._sdo_write(self.OD_PROGRAM_CTRL, 1, b'\x00'):
                time.sleep(0.5)
                val, _ = self._sdo_read(self.OD_PROGRAM_CTRL, 1, timeout=2.0)
                if val == 0:
                    logger.info(f"Node {self.node_id}: Bootloader mode confirmed")
                    return True
                logger.debug(f"Attempt {attempt+1}: mode readback = {val}")
            else:
                logger.debug(f"Attempt {attempt+1}: write failed, retrying...")
            time.sleep(0.3)
        logger.error(f"Node {self.node_id}: Failed to enter bootloader")
        return False

    def enter_application(self, retries: int = 5) -> bool:
        """Switch BMS to application mode (0x1F51:01 = 1)."""
        logger.info(f"Node {self.node_id}: Entering application mode...")
        for attempt in range(retries):
            if self._sdo_write(self.OD_PROGRAM_CTRL, 1, b'\x01'):
                time.sleep(1.0)
                val, _ = self._sdo_read(self.OD_PROGRAM_CTRL, 1, timeout=2.0)
                if val == 1:
                    logger.info(f"Node {self.node_id}: Application mode confirmed")
                    return True
            time.sleep(0.3)
        logger.error(f"Node {self.node_id}: Failed to enter application")
        return False

    # ─── Block download protocol ───

    def _block_download(self, firmware_data: bytes,
                        progress_cb: Optional[Callable] = None) -> bool:
        """
        Upload firmware via CANopen Block Download to 0x1F50:01.
        
        Protocol:
        1. Initiate block download (CCS=6) with total size
        2. Send blocks of up to 127 segments (7 bytes each)
        3. End each block with CRC and get ACK
        4. End download with full CRC
        """
        total_size = len(firmware_data)
        logger.info(f"Node {self.node_id}: Starting block download "
                     f"({total_size:,} bytes)")

        # Step 1: Initiate block download
        # CCS=6: cmd byte = 0xC4, bytes 1-3 = index+sub (0x1F50:01),
        # bytes 4-7 = total size (LE U32)
        init_data = bytes([
            0xC4,
            0x50, 0x1F, 0x01,
            total_size & 0xFF,
            (total_size >> 8) & 0xFF,
            (total_size >> 16) & 0xFF,
            (total_size >> 24) & 0xFF,
        ])
        msg = can.Message(
            arbitration_id=self.sdo_rx, is_extended_id=False,
            data=init_data
        )
        self.bus.send(msg)

        # Wait for initiate response (SCS=5, cmd=0xA4)
        t0 = time.time()
        blksize = 127
        while time.time() - t0 < 5.0:
            resp = self.bus.recv(timeout=0.5)
            if resp and resp.arbitration_id == self.sdo_tx:
                if resp.data[0] == 0xA4:
                    blksize = resp.data[4] if resp.data[4] > 0 else 127
                    logger.debug(f"Block download initiated, blksize={blksize}")
                    break
                elif resp.data[0] == 0x80:
                    abort = struct.unpack('<I', resp.data[4:8])[0]
                    logger.error(f"Init abort 0x{abort:08X}")
                    return False
        else:
            logger.error("Block download init timeout")
            return False

        # Step 2: Send blocks
        offset = 0
        block_num = 0

        while offset < total_size:
            block_num += 1
            segments_in_block = 0
            block_crc_data = bytearray()

            # Send up to blksize segments
            for i in range(min(blksize, self.SEGMENTS_PER_BLOCK)):
                if offset >= total_size:
                    break
                chunk = firmware_data[offset:offset + self.SEGMENT_DATA_SIZE]
                # Pad to 7 bytes if needed
                if len(chunk) < self.SEGMENT_DATA_SIZE:
                    chunk = chunk + bytes(self.SEGMENT_DATA_SIZE - len(chunk))

                seq = i & 0x7F
                seg_data = bytes([seq << 1]) + chunk
                msg = can.Message(
                    arbitration_id=self.sdo_rx, is_extended_id=False,
                    data=seg_data
                )
                self.bus.send(msg)
                block_crc_data.extend(firmware_data[offset:offset + self.SEGMENT_DATA_SIZE])
                segments_in_block += 1
                offset += self.SEGMENT_DATA_SIZE
                if offset > total_size:
                    offset = total_size
                    break

            # Send end-of-block with CRC
            crc = self._crc16_ccitt(block_crc_data)
            # End block cmd: 0xC9, bytes 4-5 = segments count LE, bytes 6-7 = CRC LE
            end_block = bytes([
                0xC9,
                0x50, 0x1F, 0x01,
                segments_in_block & 0xFF,
                (segments_in_block >> 8) & 0xFF,
                crc & 0xFF,
                (crc >> 8) & 0xFF,
            ])
            msg = can.Message(
                arbitration_id=self.sdo_rx, is_extended_id=False,
                data=end_block
            )
            self.bus.send(msg)

            # Wait for block ACK (0xA2)
            t1 = time.time()
            while time.time() - t1 < 10.0:
                resp = self.bus.recv(timeout=0.5)
                if resp and resp.arbitration_id == self.sdo_tx:
                    if resp.data[0] == 0xA2:
                        acked = resp.data[4] | (resp.data[5] << 8)
                        logger.debug(f"Block {block_num} acked: {acked}/{segments_in_block}")
                        break
                    elif resp.data[0] == 0x80:
                        abort = struct.unpack('<I', resp.data[4:8])[0]
                        logger.error(f"Block {block_num} abort 0x{abort:08X}")
                        return False
            else:
                logger.error(f"Block {block_num} ACK timeout")
                return False

            if progress_cb:
                progress_cb(offset, total_size, block_num)

            # Short delay between blocks
            time.sleep(0.02)

        # Step 3: End block download (0xCB)
        # bytes 4-5 = total CRC over ALL data, bytes 6-7 = 0
        total_crc = self._crc16_ccitt(firmware_data)
        end_data = bytes([
            0xCB,
            0x50, 0x1F, 0x01,
            total_crc & 0xFF,
            (total_crc >> 8) & 0xFF,
            0x00, 0x00,
        ])
        msg = can.Message(
            arbitration_id=self.sdo_rx, is_extended_id=False,
            data=end_data
        )
        self.bus.send(msg)

        # Wait for final ACK (0xA1)
        t2 = time.time()
        while time.time() - t2 < 10.0:
            resp = self.bus.recv(timeout=0.5)
            if resp and resp.arbitration_id == self.sdo_tx:
                if resp.data[0] == 0xA1:
                    logger.info("Block download completed successfully")
                    return True
                elif resp.data[0] == 0x80:
                    abort = struct.unpack('<I', resp.data[4:8])[0]
                    logger.error(f"End abort 0x{abort:08X}")
                    return False
        logger.error("Block download end timeout")
        return False

    # ─── CRC16-CCITT ───

    @staticmethod
    def _crc16_ccitt(data: bytes) -> int:
        """CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF)."""
        crc = 0xFFFF
        for byte in data:
            crc ^= (byte << 8)
            for _ in range(8):
                if crc & 0x8000:
                    crc = (crc << 1) ^ 0x1021
                else:
                    crc = (crc << 1)
                crc &= 0xFFFF
        return crc

    # ─── Intel HEX parser ───

    @staticmethod
    def parse_hex_file(path: str) -> bytes:
        """
        Parse Intel HEX file into raw binary.
        Only handles data records (type 00). Returns concatenated binary.
        """
        result = bytearray()
        base_addr = 0
        with open(path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line.startswith(':'):
                    continue
                byte_count = int(line[1:3], 16)
                address = int(line[3:7], 16)
                record_type = int(line[7:9], 16)
                data = bytes.fromhex(line[9:9 + byte_count * 2])

                if record_type == 0x00:  # Data record
                    result.extend(data)
                elif record_type == 0x02:  # Extended segment address
                    base_addr = struct.unpack('>H', data)[0] << 4
                elif record_type == 0x04:  # Extended linear address
                    base_addr = struct.unpack('>H', data)[0] << 16
                elif record_type == 0x01:  # End of file
                    break
        return bytes(result)

    # ─── Public API ───

    def update_firmware(self, hex_file_path: str,
                        progress_callback: Optional[Callable] = None) -> bool:
        """
        Update BMS firmware from Intel HEX file.

        Steps:
        1. Parse HEX file
        2. Enter bootloader mode
        3. Upload firmware via block download
        4. Verify status
        5. Enter application mode
        """
        # Parse firmware
        logger.info(f"Parsing firmware: {hex_file_path}")
        try:
            fw_data = self.parse_hex_file(hex_file_path)
        except Exception as e:
            logger.error(f"Failed to parse HEX file: {e}")
            return False

        if not fw_data:
            logger.error("Empty firmware data")
            return False

        logger.info(f"Firmware binary: {len(fw_data):,} bytes")

        # Validate size
        if len(fw_data) > 2 * 1024 * 1024:
            logger.error(f"Firmware too large: {len(fw_data)} > 2MB")
            return False

        # Step 1: Enter bootloader
        if not self.enter_bootloader():
            return False

        # Step 2: Upload firmware
        if not self._block_download(fw_data, progress_callback):
            logger.error("Block download failed")
            self.enter_application()  # Try to recover
            return False

        # Step 3: Verify firmware status
        time.sleep(0.5)
        status, abort = self._sdo_read(self.OD_FW_STATUS, 1)
        if status is not None:
            is_busy = (status & 1) == 1
            error_code = (status >> 8) & 0x7F
            if is_busy:
                logger.warning("Firmware status: busy (still programming)")
            elif error_code > 0:
                logger.error(f"Firmware error code: {error_code}")
                self.enter_application()
                return False
            else:
                logger.info("Firmware status: OK")
        elif abort is not None:
            logger.warning(f"Status read aborted 0x{abort:08X} (may be normal)")

        # Step 4: Enter application mode
        if not self.enter_application():
            logger.warning("Failed to return to application mode")
            return False

        logger.info(f"Node {self.node_id}: Firmware update complete!")
        return True


# ─── Backward compatibility ───
BMSFirmwareUpdater = FirmwareUpdater


# ─── CLI ───
if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description='SuperB Epsilon V2 Firmware Updater (CANopen Block Download)')
    parser.add_argument('--node', '-n', type=int, required=True,
                        help='BMS CANopen node ID (1-127)')
    parser.add_argument('--file', '-f', required=True,
                        help='Intel HEX firmware file')
    parser.add_argument('--interface', '-i', default='can0',
                        help='CAN interface (default: can0)')
    parser.add_argument('--bitrate', '-b', type=int, default=250000,
                        help='CAN bitrate (default: 250000)')
    parser.add_argument('--verbose', '-v', action='store_true')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s')

    bus = can.Bus(channel=args.interface, interface='socketcan',
                  bitrate=args.bitrate)

    def progress(sent, total, block):
        pct = min(100, sent * 100 // total)
        print(f"\rBlock {block}: {sent:,}/{total:,} bytes ({pct}%)",
              end='', flush=True)

    updater = FirmwareUpdater(bus, args.node)
    try:
        ok = updater.update_firmware(args.file, progress)
        print()
        if ok:
            print("✓ Update successful!")
        else:
            print("✗ Update failed!")
    finally:
        bus.shutdown()
