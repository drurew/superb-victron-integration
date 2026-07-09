#!/usr/bin/env python3
"""
BMS NVM Parameter Testing Tool
Tests which parameters can be written/reset based on flash dump analysis
"""

import time
from bms_canopen_client import CANopenSDOClient, UINT8, UINT16, UINT32, INT16, INT32

class NVMTester:
    def __init__(self, can_interface='can0'):
        self.sdo_client = CANopenSDOClient(can_interface)
        self.results = {}
    
    def test_parameter_writeable(self, node_id, index, subindex, test_value, data_type, param_name):
        """Test if a parameter can be written"""
        print(f"\n{'='*60}")
        print(f"Testing: {param_name} (0x{index:04X}:{subindex:02X})")
        print(f"{'='*60}")
        
        try:
            # Read original value
            original = self.sdo_client.read(node_id, index, subindex, data_type)
            print(f"✓ Original value: {original}")
            
            # Try to write test value
            print(f"  Attempting write: {test_value}")
            success = self.sdo_client.write(node_id, index, subindex, test_value, data_type)
            
            if not success:
                print(f"✗ Write rejected by firmware")
                self.results[param_name] = {'writable': False, 'reason': 'Write rejected'}
                return False
            
            # Read back to verify
            time.sleep(0.5)
            new_value = self.sdo_client.read(node_id, index, subindex, data_type)
            print(f"  Read back: {new_value}")
            
            if new_value == test_value:
                print(f"✓ Write SUCCESSFUL - Parameter IS writable")
                
                # Restore original value
                print(f"  Restoring original: {original}")
                self.sdo_client.write(node_id, index, subindex, original, data_type)
                time.sleep(0.5)
                restored = self.sdo_client.read(node_id, index, subindex, data_type)
                print(f"  Restored to: {restored}")
                
                self.results[param_name] = {
                    'writable': True,
                    'original': original,
                    'test_value': test_value,
                    'verified': new_value == test_value
                }
                return True
            else:
                print(f"✗ Write accepted but value unchanged - FIRMWARE PROTECTED")
                self.results[param_name] = {'writable': False, 'reason': 'Firmware ignored write'}
                return False
                
        except Exception as e:
            print(f"✗ Error: {e}")
            self.results[param_name] = {'writable': False, 'reason': str(e)}
            return False
    
    def test_charge_cycles(self, node_id):
        """Test if charge cycles counter can be written"""
        # From flash dump: Region 2 has value 129831 which could be charge cycles
        # SDO 0x6050 is documented as charge cycles
        return self.test_parameter_writeable(
            node_id, 0x6050, 0x00, 0, UINT16, "Charge Cycles"
        )
    
    def test_reset_stats_command(self, node_id):
        """Test CMD_RESET_STATS command"""
        print(f"\n{'='*60}")
        print(f"Testing: CMD_RESET_STATS (command 12)")
        print(f"{'='*60}")
        
        try:
            # Read values before reset
            print("Reading values BEFORE reset:")
            before = {}
            before['cycles'] = self.sdo_client.read(node_id, 0x6050, 0x00, UINT16)
            before['ah_expended'] = self.sdo_client.read(node_id, 0x6051, 0x00, INT16)
            before['ah_returned'] = self.sdo_client.read(node_id, 0x6052, 0x00, INT16)
            before['ah_since_eq'] = self.sdo_client.read(node_id, 0x6053, 0x00, INT32)
            
            for key, val in before.items():
                print(f"  {key}: {val}")
            
            # Send CMD_RESET_STATS (command 12 to 0x6103)
            print(f"\nSending CMD_RESET_STATS (0x6103 = 12)...")
            success = self.sdo_client.write(node_id, 0x6103, 0x00, 12, UINT8)
            
            if not success:
                print("✗ Command rejected")
                return False
            
            time.sleep(2)  # Wait for command to complete
            
            # Read values after reset
            print("\nReading values AFTER reset:")
            after = {}
            after['cycles'] = self.sdo_client.read(node_id, 0x6050, 0x00, UINT16)
            after['ah_expended'] = self.sdo_client.read(node_id, 0x6051, 0x00, INT16)
            after['ah_returned'] = self.sdo_client.read(node_id, 0x6052, 0x00, INT16)
            after['ah_since_eq'] = self.sdo_client.read(node_id, 0x6053, 0x00, INT32)
            
            changes = []
            for key in before.keys():
                print(f"  {key}: {before[key]} → {after[key]}", end="")
                if before[key] != after[key]:
                    print(f" ✓ CHANGED")
                    changes.append(key)
                else:
                    print(f" (unchanged)")
            
            if changes:
                print(f"\n✓ CMD_RESET_STATS resets: {', '.join(changes)}")
                self.results['CMD_RESET_STATS'] = {
                    'works': True,
                    'resets': changes,
                    'before': before,
                    'after': after
                }
                return True
            else:
                print(f"\n✗ CMD_RESET_STATS had no effect")
                self.results['CMD_RESET_STATS'] = {'works': False}
                return False
                
        except Exception as e:
            print(f"✗ Error: {e}")
            return False
    
    def test_clear_event_log(self, node_id):
        """Test CMD_INIT_LOG (clear event log)"""
        print(f"\n{'='*60}")
        print(f"Testing: CMD_INIT_LOG (clear event log)")
        print(f"{'='*60}")
        
        try:
            # Read event log count before
            # Note: We don't have the SDO for event log count, 
            # but we can test if the command is accepted
            print("Sending CMD_INIT_LOG (0x6103 = 8)...")
            success = self.sdo_client.write(node_id, 0x6103, 0x00, 8, UINT8)
            
            if success:
                print("✓ Command accepted - Event log likely cleared")
                self.results['CMD_INIT_LOG'] = {'works': True}
                return True
            else:
                print("✗ Command rejected")
                self.results['CMD_INIT_LOG'] = {'works': False}
                return False
                
        except Exception as e:
            print(f"✗ Error: {e}")
            return False
    
    def test_configuration_params(self, node_id):
        """Test known writable configuration parameters"""
        tests = [
            (0x6220, 0x00, UINT16, "Battery Capacity (Ah)"),
            (0x6210, 0x00, UINT16, "SOC Shutdown Level (%)"),
            (0x621D, 0x00, INT16, "Max Charge Temperature (°C)"),
            (0x6226, 0x00, UINT16, "Max Charge Current (A)"),
        ]
        
        for index, subindex, dtype, name in tests:
            # Read original
            try:
                original = self.sdo_client.read(node_id, index, subindex, dtype)
                print(f"\n{name}: Current value = {original}")
                print(f"  (Testing confirmed - parameter is readable)")
                self.results[name] = {'readable': True, 'current_value': original}
            except Exception as e:
                print(f"\n{name}: Cannot read - {e}")
                self.results[name] = {'readable': False, 'error': str(e)}
    
    def run_full_test(self, node_id):
        """Run complete test suite"""
        print(f"\n{'#'*60}")
        print(f"# BMS NVM Parameter Testing - Node {node_id}")
        print(f"# Based on flash dump analysis")
        print(f"{'#'*60}\n")
        
        print("This test will:")
        print("1. Test if charge cycles can be written (then restore)")
        print("2. Test CMD_RESET_STATS command")
        print("3. Test CMD_INIT_LOG command")
        print("4. Read known configuration parameters")
        print()
        
        input("Press ENTER to continue (Ctrl+C to abort)...")
        
        # Test 1: Charge cycles
        self.test_charge_cycles(node_id)
        
        # Test 2: Known config params (read-only test)
        self.test_configuration_params(node_id)
        
        # Test 3: Clear event log
        user_input = input("\nClear event log? This will erase log history (y/N): ")
        if user_input.lower() == 'y':
            self.test_clear_event_log(node_id)
        else:
            print("Skipping event log clear")
        
        # Test 4: Reset stats
        user_input = input("\nTest CMD_RESET_STATS? This may reset counters (y/N): ")
        if user_input.lower() == 'y':
            self.test_reset_stats_command(node_id)
        else:
            print("Skipping CMD_RESET_STATS")
        
        # Print summary
        self.print_summary()
    
    def print_summary(self):
        """Print test results summary"""
        print(f"\n{'='*60}")
        print(f"TEST RESULTS SUMMARY")
        print(f"{'='*60}\n")
        
        for param, result in self.results.items():
            print(f"{param}:")
            if 'writable' in result:
                if result['writable']:
                    print(f"  ✓ WRITABLE - Can be modified")
                else:
                    print(f"  ✗ READ-ONLY - {result.get('reason', 'Protected')}")
            elif 'works' in result:
                if result['works']:
                    print(f"  ✓ WORKS")
                    if 'resets' in result:
                        print(f"    Resets: {', '.join(result['resets'])}")
                else:
                    print(f"  ✗ DOES NOT WORK")
            elif 'readable' in result:
                if result['readable']:
                    print(f"  ✓ READABLE - Current: {result['current_value']}")
                else:
                    print(f"  ✗ NOT READABLE")
            print()
        
        print(f"{'='*60}\n")
        
        print("CONCLUSION:")
        print()
        
        if self.results.get('Charge Cycles', {}).get('writable'):
            print("✓ Battery history CAN be erased (charge cycles are writable)")
        else:
            print("✗ Charge cycles are READ-ONLY (firmware protected)")
        
        if self.results.get('CMD_RESET_STATS', {}).get('works'):
            print(f"✓ CMD_RESET_STATS works - Resets: {self.results['CMD_RESET_STATS']['resets']}")
        
        if self.results.get('CMD_INIT_LOG', {}).get('works'):
            print("✓ Event log can be cleared")
        
        print()

if __name__ == '__main__':
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python3 test_nvm_parameters.py <node_id>")
        print("Example: python3 test_nvm_parameters.py 2")
        sys.exit(1)
    
    node_id = int(sys.argv[1])
    
    tester = NVMTester()
    tester.run_full_test(node_id)
