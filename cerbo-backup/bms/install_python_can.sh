#!/bin/bash
# Dedicated script to install python3-can on Venus OS

echo "--- Starting python3-can installation ---"

# Step 1: Update opkg package lists
echo "Step 1: Running opkg update..."
opkg update
OPKG_UPDATE_STATUS=$?

if [ $OPKG_UPDATE_STATUS -ne 0 ]; then
    echo "ERROR: 'opkg update' failed with status $OPKG_UPDATE_STATUS."
    echo "This often indicates a network or repository configuration issue."
    echo "Please ensure the Cerbo has a working internet connection."
    exit 1
else
    echo "'opkg update' completed successfully."
fi

echo ""

# Step 2: Check if python3-can is already installed
echo "Step 2: Checking for existing python3-can installation..."
opkg list-installed | grep -q python3-can
ALREADY_INSTALLED=$?

if [ $ALREADY_INSTALLED -eq 0 ]; then
    echo "INFO: python3-can is already installed. Verifying it works."
    python3 -c "import can; print('Python import test successful.')"
    IMPORT_STATUS=$?
    if [ $IMPORT_STATUS -ne 0 ]; then
        echo "WARNING: python3-can is installed but cannot be imported. Attempting re-install."
    else
        echo "SUCCESS: python3-can is installed and working."
        exit 0
    fi
else
    echo "INFO: python3-can is not installed. Proceeding with installation."
fi

echo ""

# Step 3: Attempt to install python3-can
echo "Step 3: Running opkg install python3-can..."
opkg install python3-can
OPKG_INSTALL_STATUS=$?

if [ $OPKG_INSTALL_STATUS -ne 0 ]; then
    echo "ERROR: 'opkg install python3-can' failed with status $OPKG_INSTALL_STATUS."
    echo "The package may not be available in the configured repositories for this version of Venus OS."
    exit 1
else
    echo "'opkg install' command executed."
fi

echo ""

# Step 4: Final verification
echo "Step 4: Verifying installation and import..."
python3 -c "import can; print('Final Python import test successful.')"
FINAL_IMPORT_STATUS=$?

if [ $FINAL_IMPORT_STATUS -ne 0 ]; then
    echo "CRITICAL ERROR: Installation appeared to succeed, but the 'can' module cannot be imported in Python."
    echo "This indicates a broken package or an issue with the Python environment."
    exit 1
else
    echo "SUCCESS: python3-can has been successfully installed and verified."
fi

exit 0
