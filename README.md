## 1. Project Overview & Goal


This project details the modification of a commercially available, fan-made Minecraft Jukebox. The original product, purchased from Etsy, came with 3D-printed Minecraft "music discs," but its functionality was limited: inserting a disc simply triggered a random song from an SD card. The goal of this project was to re-engineer the Jukebox's internals to create the expected behavior: playing the *specific* song associated with each unique disc when it is inserted.


The solution was to augment the existing electronics with a "brain transplant." A Raspberry Pi Zero 2 W was integrated into the Jukebox. An NFC reader was positioned at the disc slot to identify which disc was inserted. The Raspberry Pi then uses its built-in Bluetooth to connect to the Jukebox's original speaker system and play the corresponding audio file.


The project is further enhanced by a custom web interface, allowing for easy management of the disc-to-song mappings and audio files directly from a browser, turning a simple prop into a fully functional and interactive music player.


## 2. System Components


This project combines the original Jukebox's hardware with new components to achieve the desired functionality.


* **Original Hardware:**

    * **Minecraft Jukebox Enclosure:** The original 3D-printed housing from the Etsy product.

    * **Original Bluetooth Audio Module:** The speaker and amplifier system that came with the Jukebox. This system is retained and used as a wireless audio output device.


* **New "Brain" Components:**

    * **Central Processing Unit:** A **Raspberry Pi Zero 2 W**, chosen for its small size, processing power, and integrated WiFi/Bluetooth capabilities.

    * **Input Module:** A **Grove - NFC Module**, purchased from The Pi Hut. This off-the-shelf module is based on the reliable NXP PN532 chipset and is used to read the NFC tags embedded in the music discs.

    * **Operating System:** **DietPi**, a lightweight Debian-based OS ideal for this embedded application.


## 3. System Architecture & Connectivity


The architecture is designed to be a minimally invasive upgrade to the original product.


* **NFC Reader Wiring:** The Grove NFC module is the only new component physically wired to the Raspberry Pi. It communicates over a UART serial interface, allowing the Pi to receive the unique ID of each scanned disc.

    * **Connection Type:** 4-wire Grove to GPIO connection.

    * **Wiring:**

        * NFC `VCC` → Raspberry Pi `5V`

        * NFC `GND` → Raspberry Pi `GND`

        * NFC `TX` → Raspberry Pi `RX` (GPIO15)

        * NFC `RX` → Raspberry Pi `TX` (GPIO14)


* **Audio Output Connectivity:** The connection to the audio system is entirely **wireless**. The Raspberry Pi acts as a Bluetooth client, connecting to the Jukebox's original Bluetooth speaker. There is **no serial or wired connection** between the Pi and the audio module. This clever approach leverages the existing hardware without complex reverse-engineering.


## 4. Software Architecture & Application Logic


The software stack is built on Python, using a multi-threaded Flask application to manage all system functions.


#### 4.1 Core Framework & Dependencies


The application uses the **Flask** web framework. Key Python dependencies from `requirements.txt` include:

* `Flask`: To create the web server for the management UI.

* `pyserial` and `adafruit-circuitpython-pn532`: To communicate with the Grove NFC module via the UART port.


#### 4.2 Bluetooth Audio Management


Instead of sending serial commands, the Python application plays audio by making system calls to a command-line audio player (like `mpg123` or `aplay`). The script ensures that on startup, the Raspberry Pi automatically scans for and connects to the Jukebox's known Bluetooth address. The web interface (`bluetooth.html`) provides a user-friendly way to manage this connection if needed.


#### 4.3 Application Structure (`main.py`)


The `main.py` script uses a background thread for NFC polling to ensure the web UI remains responsive.


* **Main Thread:** Runs the Flask web server, handling HTTP requests for the management interface.

* **NFC Thread (`nfc_handler.py`):** Runs in the background, continuously polling the Grove NFC module. When a disc is scanned, its UID is read and made available to the main application.

* **Playback Logic:** When a new UID is detected, the main application looks it up in a `tags.json` file. If a match is found, it triggers the associated audio file to be played over the established Bluetooth connection.


```python

# Conceptual snippet of playback logic in main.py

import subprocess


def play_song_for_uid(uid):

    tag_map = load_tags_from_json() # Load UID-to-song mapping

    if uid in tag_map:

        song_path = tag_map[uid]

        # Use a command-line player to output to the Bluetooth sink

        subprocess.run(["mpg123", song_path])

```


## 5. Web Management Interface


The web UI is a critical feature, making the modified Jukebox easy for a non-technical user to manage.


* **Music Management (`manage_music.html`):** Allows for uploading new songs (MP3, etc.) to the Raspberry Pi.

* **NFC Tag Registration (`nfc_register.html`):** A simple workflow to link a physical disc to a song. The user clicks "Scan," inserts a disc, and then selects the desired song from a dropdown menu to create the association.

* **Settings & Bluetooth:** Pages to control system volume and manage the Bluetooth connection to the speaker.


## 6. Deployment & Autostart


The `jukebox_start.sh` script, managed by a `systemd` service, ensures the entire Python application starts automatically when the Jukebox is powered on. This makes the device a true, self-contained appliance—plug it in, and it just works. 
