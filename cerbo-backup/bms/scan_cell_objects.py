#!/usr/bin/env python3
"""
Scan for cell voltage and temperature SDO objects
Try various object ranges to find where individual cell data is stored
"""

import sys
sys.path.insert(0, '/run/media/batchete/2EDB740970069929/Users/me/Desktop/Be in Charge Software Setup 1.7.0/fulldump/master/victron-bms')

from canopen_client import CANopenClient

def scan_for_cells():
    client = CANopenClient('can0', 250000)
    node_id = 2
    
    print("Scanning for cell voltage objects...")
    print("=" * 80)
    
    # Test ranges where cell data might be
    test_ranges = [
        (0x3000, 0x30FF, "0x3000 range"),
        (0x4000, 0x40FF, "0x4000 range"),
        (0x5000, 0x50FF, "0x5000 range"),
    ]
    
    found_objects = []
    
    for start, end, name in test_ranges:
        print(f"\nScanning {name}...")
        for idx in range(start, end + 1):
            # Try subindexes 0-10 for each index
            for subidx in range(0, 11):
                try:
                    raw_data, abort = client.read_sdo(node_id, idx, subidx)
                    if raw_data and not abort:
                        # Try different data types
                        try:
                            val_u16 = client.decode_value(raw_data, 'UINT16')
                            val_i16 = client.decode_value(raw_data, 'INT16')
                            val_u32 = client.decode_value(raw_data, 'UINT32')
                            
                            # Check if value looks like a cell voltage (2800-3600 mV for LiFePO4)
                            is_voltage = 2800 <= val_u16 <= 3600
                            # Check if value looks like temperature (-200 to 600 in 0.1°C = -20°C to 60°C)
                            is_temp = -200 <= val_i16 <= 600
                            
                            if is_voltage or is_temp:
                                obj_str = f"0x{idx:04X}:{subidx:02X}"
                                print(f"  {obj_str:15} UINT16={val_u16:5} INT16={val_i16:5} UINT32={val_u32:8} ", end="")
                                if is_voltage:
                                    print(f"<-- VOLTAGE? ({val_u16/1000.0:.3f}V)", end="")
                                if is_temp:
                                    print(f"<-- TEMP? ({val_i16/10.0:.1f}°C)", end="")
                                print()
                                found_objects.append((idx, subidx, val_u16, val_i16))
                        except:
                            pass
                except:
                    pass
    
    print("\n" + "=" * 80)
    print("SUMMARY - Potential cell data objects:")
    print("=" * 80)
    for idx, subidx, val_u16, val_i16 in found_objects:
        obj_str = f"0x{idx:04X}:{subidx:02X}"
        print(f"{obj_str:15} UINT16={val_u16:5} ({val_u16/1000.0:.3f}V)  INT16={val_i16:5} ({val_i16/10.0:.1f}°C)")
    
    # Also check the known min/max objects for reference
    print("\n" + "=" * 80)
    print("REFERENCE - Known min/max objects:")
    print("=" * 80)
    try:
        raw_data, abort = client.read_sdo(node_id, 0x2022, 0x01)
        if raw_data and not abort:
            min_v = client.decode_value(raw_data, 'UINT16')
            print(f"0x2022:01 (Min cell V) = {min_v} mV = {min_v/1000.0:.3f} V")
        
        raw_data, abort = client.read_sdo(node_id, 0x2022, 0x02)
        if raw_data and not abort:
            max_v = client.decode_value(raw_data, 'UINT16')
            print(f"0x2022:02 (Max cell V) = {max_v} mV = {max_v/1000.0:.3f} V")
        
        raw_data, abort = client.read_sdo(node_id, 0x2023, 0x01)
        if raw_data and not abort:
            min_t = client.decode_value(raw_data, 'INT16')
            print(f"0x2023:01 (Min cell T) = {min_t} (0.1°C) = {min_t/10.0:.1f} °C")
        
        raw_data, abort = client.read_sdo(node_id, 0x2023, 0x02)
        if raw_data and not abort:
            max_t = client.decode_value(raw_data, 'INT16')
            print(f"0x2023:02 (Max cell T) = {max_t} (0.1°C) = {max_t/10.0:.1f} °C")
    except Exception as e:
        print(f"Error reading min/max: {e}")

if __name__ == '__main__':
    scan_for_cells()
