#!/bin/bash
# jukebox_start.sh - Start JukeboxManager only if internet and bluetooth are up

# Wait for internet connection
while ! ping -c 1 -W 1 8.8.8.8 >/dev/null 2>&1; do
    echo "Waiting for internet connection..."
    sleep 2
done

echo "Internet connection detected."

# Wait for bluetooth service to be active
while ! systemctl is-active --quiet bluetooth; do
    echo "Waiting for bluetooth service..."
    sleep 2
done

echo "Bluetooth service is active."

# Activate Python virtual environment if needed
source /home/user/.virtualenvs/JukeboxManager/bin/activate

# Start the JukeboxManager Flask app
cd /home/user/JukeboxManager || exit
exec python3 main.py

