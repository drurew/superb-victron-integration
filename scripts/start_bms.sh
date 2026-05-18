#!/bin/sh
# Start the SuperB BMS driver on a Cerbo GX
#
# Uses the compiled C driver by default. Falls back to the Python
# reference driver if the C binary is not found.

BMS_DIR="/data/bms"
CAN_IFACE="${1:-vecan0}"

if [ -x "$BMS_DIR/victron-bms" ]; then
    # C driver (production)
    exec "$BMS_DIR/victron-bms" "$CAN_IFACE"
elif [ -f "$BMS_DIR/src/victron_bms_multi.py" ]; then
    # Python driver (reference)
    exec python3 "$BMS_DIR/src/victron_bms_multi.py" \
        --interface "$CAN_IFACE" \
        --log-file /var/log/victron-bms.log
else
    echo "ERROR: No BMS driver found in $BMS_DIR" >&2
    exit 1
fi
