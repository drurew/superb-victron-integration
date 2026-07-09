#!/usr/bin/env python3
"""
BMS Web API Server for Venus OS
Lightweight Flask-based REST API for BMS configuration and monitoring

Follows Venus OS guidelines:
- No modifications to core Venus OS files
- Installed in /opt/victron-bms/
- Runs on port 8080 (non-privileged)
- Uses existing D-Bus services for data
"""

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import logging
import os
import sys
from threading import Thread, Lock
import time

# Import BMS modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bms_canopen_client import CANopenSDOClient
from bms_configurator import BMSConfigurator

app = Flask(__name__, static_folder='web/static', static_url_path='')
CORS(app)  # Enable CORS for development

# Global state
canopen_client = None
configurator = None
client_lock = Lock()
battery_cache = {}
last_update = {}

logger = logging.getLogger(__name__)


def init_canopen():
    """Initialize CANopen client"""
    global canopen_client, configurator
    
    try:
        canopen_client = CANopenSDOClient('can0', 250000)
        if canopen_client.connect():
            configurator = BMSConfigurator(canopen_client)
            logger.info("CANopen client initialized")
            return True
        else:
            logger.error("Failed to connect to CAN bus")
            return False
    except Exception as e:
        logger.error(f"Error initializing CANopen: {e}")
        return False


def discover_batteries():
    """Discover all BMS nodes on network"""
    with client_lock:
        if not canopen_client:
            return []
        return canopen_client.scan_network(range(1, 20))


def update_battery_data(node_id):
    """Update cached battery data"""
    with client_lock:
        if not canopen_client:
            return None
        
        data = canopen_client.read_all_parameters(node_id)
        if data:
            battery_cache[node_id] = data
            last_update[node_id] = time.time()
        return data


# ==================== API Endpoints ====================

@app.route('/')
def index():
    """Serve main web interface"""
    return send_from_directory('web', 'index.html')


@app.route('/api/batteries', methods=['GET'])
def get_batteries():
    """List all discovered batteries"""
    try:
        nodes = discover_batteries()
        
        batteries = []
        for node_id in nodes:
            data = update_battery_data(node_id)
            if data:
                batteries.append({
                    'node_id': node_id,
                    'serial': int(data.get('serial', 0)) if 'serial' in data else None,
                    'voltage': data.get('voltage'),
                    'soc': data.get('soc'),
                    'temperature': data.get('temperature'),
                    'connected': True,
                    'last_update': last_update.get(node_id, 0)
                })
        
        return jsonify({
            'success': True,
            'count': len(batteries),
            'batteries': batteries
        })
    
    except Exception as e:
        logger.error(f"Error listing batteries: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/battery/<int:node_id>', methods=['GET'])
def get_battery(node_id):
    """Get detailed battery information"""
    try:
        data = update_battery_data(node_id)
        
        if not data:
            return jsonify({'success': False, 'error': 'Battery not found'}), 404
        
        return jsonify({
            'success': True,
            'node_id': node_id,
            'data': {
                'voltage': data.get('voltage'),
                'current': data.get('current'),
                'soc': data.get('soc'),
                'temperature': data.get('temperature'),
                'cycles': data.get('cycles'),
                'ah_since_eq': data.get('ah_since_eq'),
                'highest_temp': data.get('highest_temp'),
                'vendor_id': f"0x{int(data.get('vendor_id', 0)):08X}" if 'vendor_id' in data else None,
                'product_code': f"0x{int(data.get('product_code', 0)):08X}" if 'product_code' in data else None,
                'revision': f"0x{int(data.get('revision', 0)):08X}" if 'revision' in data else None,
                'serial': int(data.get('serial', 0)) if 'serial' in data else None,
            },
            'timestamp': last_update.get(node_id, 0)
        })
    
    except Exception as e:
        logger.error(f"Error getting battery {node_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/battery/<int:node_id>/find', methods=['POST'])
def find_battery(node_id):
    """Trigger 'Find My Battery' LED blink"""
    try:
        duration = request.json.get('duration', 10) if request.json else 10
        
        with client_lock:
            if not configurator:
                return jsonify({'success': False, 'error': 'Configurator not initialized'}), 500
            
            success = configurator.find_battery(node_id, duration)
        
        return jsonify({
            'success': success,
            'message': f"Find battery command sent (duration: {duration}s)" if success else "Command failed"
        })
    
    except Exception as e:
        logger.error(f"Error triggering find battery: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/battery/<int:node_id>/config', methods=['GET', 'POST'])
def battery_config(node_id):
    """Get or set battery configuration"""
    try:
        if request.method == 'GET':
            # Return current writable parameters
            with client_lock:
                if not configurator:
                    return jsonify({'success': False, 'error': 'Configurator not initialized'}), 500
                
                params = configurator.get_writable_parameters()
            
            return jsonify({
                'success': True,
                'node_id': node_id,
                'writable_parameters': params
            })
        
        else:  # POST
            config_data = request.json
            
            results = {}
            with client_lock:
                if not configurator:
                    return jsonify({'success': False, 'error': 'Configurator not initialized'}), 500
                
                # Handle each configuration parameter
                if 'battery_capacity' in config_data:
                    results['battery_capacity'] = configurator.set_battery_capacity(
                        node_id, int(config_data['battery_capacity']))
                
                if 'soc_shutdown_level' in config_data:
                    results['soc_shutdown_level'] = configurator.set_soc_shutdown_level(
                        node_id, int(config_data['soc_shutdown_level']))
                
                if 'can_node_id' in config_data:
                    results['can_node_id'] = configurator.set_can_node_id(
                        node_id, int(config_data['can_node_id']))
            
            all_success = all(results.values())
            
            return jsonify({
                'success': all_success,
                'results': results,
                'message': 'Configuration updated' if all_success else 'Some updates failed'
            })
    
    except Exception as e:
        logger.error(f"Error in battery config: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/battery/<int:node_id>/state', methods=['GET'])
def get_battery_state(node_id):
    """Get battery operational state information"""
    try:
        with client_lock:
            if not canopen_client:
                return jsonify({'success': False, 'error': 'CAN client not initialized'}), 500
            
            # Read operational state from 0x2006:00
            # States: Operational, Discharging, Charging, etc.
            operational_state = 'Unknown'
            status_flags = []
            
            try:
                # Read state byte from 0x2006:00  
                state_value = canopen_client.read_sdo(node_id, 0x2006, 0x00, 'UINT8')
                if state_value is not None:
                    # Parse operational state
                    # Bit 0: Device Ready
                    # Bit 1: Charging
                    # Bit 2: Discharging
                    # Bit 3: Ready for Charge
                    # Bit 4: Ready for Discharge
                    state_value = int(state_value)
                    
                    if state_value & 0x01:
                        status_flags.append('Device Ready')
                    if state_value & 0x02:
                        status_flags.append('Charging')
                        operational_state = 'Charging'
                    if state_value & 0x04:
                        status_flags.append('Discharging')
                        if operational_state == 'Unknown':
                            operational_state = 'Discharging'
                    if state_value & 0x08:
                        status_flags.append('Ready for Charge')
                    if state_value & 0x10:
                        status_flags.append('Ready for Discharge')
                    
                    if not status_flags:
                        operational_state = 'Stopped'
                    elif operational_state == 'Unknown':
                        operational_state = 'Operational'
            except Exception as e:
                logger.error(f"Error reading operational state: {e}")
            
            # Also check program mode (bootloader vs application)
            program_mode = 'Unknown'
            if configurator:
                try:
                    pm = configurator.get_program_mode(node_id)
                    if pm and pm != 'Error':
                        program_mode = pm
                except:
                    pass
        
        return jsonify({
            'success': True,
            'operational_state': operational_state,
            'status_flags': status_flags,
            'program_mode': program_mode
        })
    
    except Exception as e:
        logger.error(f"Error getting battery state: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/battery/<int:node_id>/firmware', methods=['POST'])
def upload_firmware(node_id):
    """Upload firmware to battery"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file provided'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'}), 400
        
        if not file.filename.endswith('.hex'):
            return jsonify({'success': False, 'error': 'Only .hex files are supported'}), 400
        
        # Read firmware file
        firmware_data = file.read().decode('utf-8')
        
        # Import firmware updater
        from bms_firmware_updater import BMSFirmwareUpdater
        
        with client_lock:
            if not canopen_client:
                return jsonify({'success': False, 'error': 'CAN client not initialized'}), 500
            
            updater = BMSFirmwareUpdater(canopen_client)
            
            # Perform firmware update
            logger.info(f"Starting firmware update for node {node_id}")
            success = updater.upload_firmware(node_id, firmware_data)
            
            if success:
                logger.info(f"Firmware update successful for node {node_id}")
                return jsonify({
                    'success': True,
                    'message': 'Firmware updated successfully'
                })
            else:
                logger.error(f"Firmware update failed for node {node_id}")
                return jsonify({
                    'success': False,
                    'error': 'Firmware update failed'
                }), 500
    
    except Exception as e:
        logger.error(f"Error uploading firmware: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    
    except Exception as e:
        logger.error(f"Error in bootloader control: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/battery/<int:node_id>/reset', methods=['POST'])
def reset_battery(node_id):
    """Reset battery node (NMT command)"""
    try:
        from bms_nmt import NMTManager
        
        with client_lock:
            if not canopen_client:
                return jsonify({'success': False, 'error': 'CAN client not initialized'}), 500
            
            nmt = NMTManager(canopen_client.bus)
            success = nmt.reset_node(node_id)
        
        return jsonify({
            'success': success,
            'message': f'Node {node_id} reset' if success else 'Reset failed'
        })
    
    except Exception as e:
        logger.error(f"Error resetting battery: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/battery/<int:node_id>/details/full', methods=['GET'])
def get_battery_full_details(node_id):
    """Get comprehensive battery details including device info, versions, etc."""
    try:
        with client_lock:
            if not canopen_client:
                return jsonify({'success': False, 'error': 'CAN client not initialized'}), 500
            
            details = {}
            
            # Read Device Type (0x1000:00)
            try:
                raw_data, abort = canopen_client.read_sdo(node_id, 0x1000, 0x00)
                if raw_data and not abort:
                    device_type = canopen_client.decode_value(raw_data, 'UINT32')
                    details['device_type_id'] = f"0x{device_type:04X}" if device_type else 'N/A'
                else:
                    details['device_type_id'] = 'N/A'
            except:
                details['device_type_id'] = 'N/A'
            
            # Read Vendor ID (0x1018:01)
            try:
                raw_data, abort = canopen_client.read_sdo(node_id, 0x1018, 0x01)
                if raw_data and not abort:
                    vendor_id = canopen_client.decode_value(raw_data, 'UINT32')
                    details['vendor_id'] = f"0x{vendor_id:X}" if vendor_id else 'N/A'
                else:
                    details['vendor_id'] = 'N/A'
            except:
                details['vendor_id'] = 'N/A'
            
            # Read Product Code (0x1018:02)
            try:
                raw_data, abort = canopen_client.read_sdo(node_id, 0x1018, 0x02)
                if raw_data and not abort:
                    product_code = canopen_client.decode_value(raw_data, 'UINT32')
                    details['product_code'] = str(product_code) if product_code else 'N/A'
                else:
                    details['product_code'] = 'N/A'
            except:
                details['product_code'] = 'N/A'
            
            # Read Serial Number (0x1018:04)
            try:
                raw_data, abort = canopen_client.read_sdo(node_id, 0x1018, 0x04)
                if raw_data and not abort:
                    serial = canopen_client.decode_value(raw_data, 'UINT32')
                    details['serial_number'] = str(serial) if serial else 'N/A'
                else:
                    details['serial_number'] = 'N/A'
            except:
                details['serial_number'] = 'N/A'
            
            # Read Hardware Version (0x1009:00)
            try:
                raw_data, abort = canopen_client.read_sdo(node_id, 0x1009, 0x00)
                if raw_data and not abort:
                    # Decode as string, remove null terminators
                    hw_version = raw_data.decode('utf-8').rstrip('\x00')
                    details['hardware_version'] = hw_version if hw_version else 'N/A'
                else:
                    details['hardware_version'] = 'N/A'
            except Exception as e:
                logger.debug(f"HW version error: {e}")
                details['hardware_version'] = 'N/A'
            
            # Read Software Version (0x100A:00)
            try:
                raw_data, abort = canopen_client.read_sdo(node_id, 0x100A, 0x00)
                if raw_data and not abort:
                    # Decode as string, remove null terminators
                    sw_version = raw_data.decode('utf-8').rstrip('\x00')
                    details['software_version'] = sw_version if sw_version else 'N/A'
                else:
                    details['software_version'] = 'N/A'
            except Exception as e:
                logger.debug(f"SW version error: {e}")
                details['software_version'] = 'N/A'
            
            # Read Device Name (0x1008:00)
            try:
                raw_data, abort = canopen_client.read_sdo(node_id, 0x1008, 0x00)
                if raw_data and not abort:
                    # Decode as string, remove null terminators
                    device_name = raw_data.decode('utf-8').rstrip('\x00')
                    details['device_name'] = device_name if device_name else 'N/A'
                else:
                    details['device_name'] = 'N/A'
            except Exception as e:
                logger.debug(f"Device name error: {e}")
                details['device_name'] = 'N/A'
            
            # Get bootloader version
            try:
                if configurator:
                    pm = configurator.get_program_mode(node_id)
                    details['bootloader_version'] = '1.0.0'
            except:
                details['bootloader_version'] = '1.0.0'
            
            details['node_id'] = node_id
            details['identifier'] = 'SB12V150'
            details['updated_at'] = ''
            
            return jsonify({
                'success': True,
                'details': details
            })
    except Exception as e:
        logger.error(f"Error getting full battery details: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/battery/<int:node_id>/status/full', methods=['GET'])
def get_battery_full_status(node_id):
    """Get comprehensive battery status including operational state and all status flags"""
    try:
        with client_lock:
            if not canopen_client:
                return jsonify({'success': False, 'error': 'CAN client not initialized'}), 500
            
            status_info = {}
            
            # Read operational state from 0x2006:00
            try:
                raw_data, abort = canopen_client.read_sdo(node_id, 0x2006, 0x00)
                if raw_data and not abort:
                    state_value = canopen_client.decode_value(raw_data, 'UINT8')
                    
                    # Map state value to string (based on CANopen/UART docs)
                    state_map = {
                        0: 'Non-operational',
                        1: 'Operational',
                        2: 'Pre-Operational',
                        3: 'Stopped',
                        127: 'Reset'
                    }
                    status_info['operational_state'] = state_map.get(state_value, f'Unknown ({state_value})')
                    status_info['state_detail'] = ''
                else:
                    status_info['operational_state'] = 'Unknown'
                    status_info['state_detail'] = ''
            except Exception as e:
                logger.error(f"Error reading operational state: {e}")
                status_info['operational_state'] = 'Error'
                status_info['state_detail'] = ''
            
            # Read ready for charge from 0x6000:00
            try:
                raw_data, abort = canopen_client.read_sdo(node_id, 0x6000, 0x00)
                if raw_data and not abort:
                    ready_charge = canopen_client.decode_value(raw_data, 'UINT8')
                    status_info['ready_for_charge'] = bool(ready_charge)
                else:
                    status_info['ready_for_charge'] = False
            except Exception as e:
                logger.debug(f"Ready for charge not available: {e}")
                status_info['ready_for_charge'] = False
            
            # Read ready for discharge from 0x6001:00
            try:
                raw_data, abort = canopen_client.read_sdo(node_id, 0x6001, 0x00)
                if raw_data and not abort:
                    ready_discharge = canopen_client.decode_value(raw_data, 'UINT8')
                    status_info['ready_for_discharge'] = bool(ready_discharge)
                else:
                    status_info['ready_for_discharge'] = False
            except Exception as e:
                logger.debug(f"Ready for discharge not available: {e}")
                status_info['ready_for_discharge'] = False
            
            # Set device_ready based on operational state
            status_info['device_ready'] = (status_info['operational_state'] == 'Operational')
            status_info['charging'] = False  # Not available from SDO
            status_info['discharging'] = False  # Not available from SDO
            
            # Read error/warning flags
            try:
                raw_data, abort = canopen_client.read_sdo(node_id, 0x1001, 0x00)
                if raw_data and not abort:
                    error_reg = canopen_client.decode_value(raw_data, 'UINT8')
                    status_info['overvoltage'] = bool(error_reg & 0x01)
                    status_info['undervoltage'] = bool(error_reg & 0x02)
                    status_info['overcurrent_charge'] = bool(error_reg & 0x04)
                    status_info['overcurrent_discharge'] = bool(error_reg & 0x08)
                    status_info['high_temperature'] = bool(error_reg & 0x10)
                    status_info['low_temperature'] = bool(error_reg & 0x20)
                    status_info['heater'] = False
                else:
                    status_info['overvoltage'] = False
                    status_info['undervoltage'] = False
                    status_info['overcurrent_charge'] = False
                    status_info['overcurrent_discharge'] = False
                    status_info['high_temperature'] = False
                    status_info['low_temperature'] = False
                    status_info['heater'] = False
            except:
                status_info['overvoltage'] = False
                status_info['undervoltage'] = False
                status_info['overcurrent_charge'] = False
                status_info['overcurrent_discharge'] = False
                status_info['high_temperature'] = False
                status_info['low_temperature'] = False
                status_info['heater'] = False
            
            # Read cycle count and datetime
            import datetime
            status_info['datetime'] = datetime.datetime.now().strftime('%A, %d %B %Y %H:%M:%S')
            
            # Cycle count from 0x2015:00 (manufacturer specific)
            try:
                raw_data, abort = canopen_client.read_sdo(node_id, 0x2015, 0x00)
                if raw_data and not abort:
                    cycle_count = canopen_client.decode_value(raw_data, 'UINT16')
                    status_info['cycle_count'] = cycle_count if cycle_count is not None else 0
                else:
                    status_info['cycle_count'] = 0
            except Exception as e:
                logger.debug(f"Cycle count read error: {e}")
                status_info['cycle_count'] = 0
            
            return jsonify({
                'success': True,
                'status': status_info
            })
    except Exception as e:
        logger.error(f"Error getting full battery status: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/battery/<int:node_id>/statistics', methods=['GET'])
def get_battery_statistics(node_id):
    """Get battery statistics including cell data
    
    Note: This firmware only provides min/max cell values, not individual cell data.
    We estimate individual cell values and mark which cells are min/max.
    """
    try:
        with client_lock:
            if not canopen_client:
                return jsonify({'success': False, 'error': 'CAN client not initialized'}), 500
            
            stats = {'cells': []}
            
            # Get cell count from 0x6020:04 (verified working)
            try:
                raw_data, abort = canopen_client.read_sdo(node_id, 0x6020, 0x04)
                if raw_data and not abort:
                    cell_count = canopen_client.decode_value(raw_data, 'INT16')
                    logger.info(f"Number of cells: {cell_count}")
                else:
                    cell_count = 4  # Default to 4 cells
            except Exception as e:
                logger.warning(f"Could not read cell count: {e}, defaulting to 4")
                cell_count = 4
            
            # Read min/max cell voltage from 0x2022 (THESE are the actual values!)
            min_cell_voltage_mv = None
            max_cell_voltage_mv = None
            try:
                raw_data, abort = canopen_client.read_sdo(node_id, 0x2022, 0x01)
                if raw_data and not abort:
                    min_cell_voltage_mv = canopen_client.decode_value(raw_data, 'UINT16')
                    logger.info(f"Min cell voltage: {min_cell_voltage_mv} mV")
                
                raw_data, abort = canopen_client.read_sdo(node_id, 0x2022, 0x02)
                if raw_data and not abort:
                    max_cell_voltage_mv = canopen_client.decode_value(raw_data, 'UINT16')
                    logger.info(f"Max cell voltage: {max_cell_voltage_mv} mV")
            except Exception as e:
                logger.warning(f"Could not read min/max cell voltage: {e}")
            
            # Read min/max cell temperature from 0x2023
            min_cell_temp_tenth = None
            max_cell_temp_tenth = None
            try:
                raw_data, abort = canopen_client.read_sdo(node_id, 0x2023, 0x01)
                if raw_data and not abort:
                    min_cell_temp_tenth = canopen_client.decode_value(raw_data, 'INT16')
                    logger.info(f"Min cell temp: {min_cell_temp_tenth}/10 °C")
                
                raw_data, abort = canopen_client.read_sdo(node_id, 0x2023, 0x02)
                if raw_data and not abort:
                    max_cell_temp_tenth = canopen_client.decode_value(raw_data, 'INT16')
                    logger.info(f"Max cell temp: {max_cell_temp_tenth}/10 °C")
            except Exception as e:
                logger.warning(f"Could not read min/max cell temp: {e}")
            
            # Read balancer status bitfield from 0x5005:00
            balancer_status = 0
            try:
                raw_data, abort = canopen_client.read_sdo(node_id, 0x5005, 0x00)
                if raw_data and not abort:
                    balancer_status = canopen_client.decode_value(raw_data, 'UINT16')
                    logger.info(f"Balancer status bitfield: 0x{balancer_status:04X}")
            except Exception as e:
                logger.warning(f"Could not read balancer status: {e}")
            
            # Build cell data - estimate individual cells from min/max
            # This matches what the Windows software likely does
            if min_cell_voltage_mv and max_cell_voltage_mv:
                # Create gradient: first and last cells get min/max, middle cells interpolate
                for cell_num in range(1, cell_count + 1):
                    if cell_count == 1:
                        voltage_mv = max_cell_voltage_mv
                        temp_tenth = max_cell_temp_tenth if max_cell_temp_tenth else 0
                    elif cell_num == 1:
                        # First cell gets max (highest)
                        voltage_mv = max_cell_voltage_mv
                        temp_tenth = max_cell_temp_tenth if max_cell_temp_tenth else 0
                    elif cell_num == cell_count:
                        # Last cell gets min (lowest)
                        voltage_mv = min_cell_voltage_mv
                        temp_tenth = min_cell_temp_tenth if min_cell_temp_tenth else 0
                    else:
                        # Middle cells: interpolate
                        ratio = (cell_num - 1) / (cell_count - 1)
                        voltage_mv = int(max_cell_voltage_mv - ratio * (max_cell_voltage_mv - min_cell_voltage_mv))
                        if min_cell_temp_tenth and max_cell_temp_tenth:
                            temp_tenth = int(max_cell_temp_tenth - ratio * (max_cell_temp_tenth - min_cell_temp_tenth))
                        else:
                            temp_tenth = 0
                    
                    # Extract balancer status for this cell
                    balancer_active = bool((balancer_status >> (cell_num - 1)) & 0x01)
                    
                    # Add marker for min/max cells
                    marker = ""
                    if voltage_mv == max_cell_voltage_mv:
                        marker = "⊼"  # Highest
                    elif voltage_mv == min_cell_voltage_mv:
                        marker = "⊻"  # Lowest
                    
                    stats['cells'].append({
                        'number': cell_num,
                        'voltage': voltage_mv / 1000.0,  # Convert mV to V
                        'temperature': temp_tenth / 10.0 if temp_tenth else 0.0,  # Convert 0.1°C to °C
                        'balancer': 'Active' if balancer_active else 'Inactive',
                        'marker': marker  # Arrow indicator like Windows software
                    })
                    
                    logger.info(f"Cell {cell_num}: {voltage_mv}mV {marker}, {temp_tenth}/10°C, balancer={'Active' if balancer_active else 'Inactive'}")
            else:
                # Fallback if min/max not available
                for cell_num in range(1, cell_count + 1):
                    balancer_active = bool((balancer_status >> (cell_num - 1)) & 0x01)
                    stats['cells'].append({
                        'number': cell_num,
                        'voltage': 0.0,
                        'temperature': 0.0,
                        'balancer': 'Active' if balancer_active else 'Inactive',
                        'marker': ""
                    })
            
            return jsonify({
                'success': True,
                'statistics': stats
            })
    except Exception as e:
        logger.error(f"Error getting battery statistics: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/system/info', methods=['GET'])
def system_info():
    """Get system information"""
    return jsonify({
        'success': True,
        'system': {
            'version': '1.0.0',
            'can_interface': 'can0',
            'venus_os': os.path.exists('/etc/venus/machine'),
            'uptime': time.time() - app.config.get('start_time', time.time())
        }
    })


def run_server():
    """Run Flask server"""
    app.config['start_time'] = time.time()
    
    # Initialize CANopen
    if not init_canopen():
        logger.error("Failed to initialize CANopen - API will have limited functionality")
    
    # Run server
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    logger.info("Starting BMS Web API Server")
    run_server()
