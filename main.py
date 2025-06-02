# Jukebox Manager
from flask import Flask, render_template, request, redirect, url_for, send_file, jsonify, current_app, flash
import os
import subprocess
import threading
import json
import time
import signal
import board
import busio
import adafruit_pn532.i2c
import adafruit_pn532.uart
import hashlib
from nfc_handler import NFCController, logger
import random  # Added for idle mode
import atexit  # Added for saving settings
from werkzeug.utils import secure_filename

MUSIC_DIR = os.path.join(os.getcwd(), 'music')
BLUETOOTH_SPEAKER_MAC = "1B:CA:C0:47:8A:CF"

# Store auto-connect devices in a JSON file
AUTO_CONNECT_FILE = os.path.join(os.getcwd(), 'auto_connect.json')
CURRENT_DEVICE_FILE = os.path.join(os.getcwd(), 'current_bluetooth_device.json')
NFC_MAP_FILE = os.path.join(os.getcwd(), 'nfc_map.json')
# Hash-to-song map for quick lookup
HASH_MAP_FILE = os.path.join(os.getcwd(), 'hash_map.json')
hash_map = {}

SETTINGS_FILE = os.path.join(os.getcwd(), 'settings.json')
print(f"Using music directory: {MUSIC_DIR}")

app = Flask(__name__)
app.secret_key = os.urandom(24)

current_playback_process = None
playback_lock = threading.Lock()
current_playing = None

# Default settings
settings = {
    "loop_nfc_song": False,
    "idle_mode": "do_nothing",
    "select_songs": []
}

def save_settings():
    try:
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(settings, f, indent=4)
        print("Settings saved.")
    except Exception as e:
        print(f"Error saving settings: {e}")

def load_settings():
    global settings
    default_settings_template = {
        "loop_nfc_song": False,
        "idle_mode": "do_nothing",
        "select_songs": []
    }
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r') as f:
                loaded_settings = json.load(f)
                for key in default_settings_template:
                    if key in loaded_settings:
                        # Basic type validation
                        if key == "select_songs" and not isinstance(loaded_settings[key], list):
                            settings[key] = default_settings_template[key]
                        elif key == "loop_nfc_song" and not isinstance(loaded_settings[key], bool):
                            settings[key] = default_settings_template[key]
                        elif key == "idle_mode" and not isinstance(loaded_settings[key], str):
                            settings[key] = default_settings_template[key]
                        else:
                            settings[key] = loaded_settings[key]
                    else:
                        settings[key] = default_settings_template[key] # Key missing, use default
            print("Settings loaded.")
        except json.JSONDecodeError:
            print(f"Error: Could not decode {SETTINGS_FILE}. Using default settings.")
            settings = default_settings_template.copy()
        except Exception as e:
            print(f"Error loading settings from {SETTINGS_FILE}: {e}. Using default settings.")
            settings = default_settings_template.copy()
    else:
        print("Settings file not found. Using default settings.")
        settings = default_settings_template.copy()

# Load settings at startup
load_settings()

# Register save_settings to be called on exit
atexit.register(save_settings)

# NFC hash read callback
def on_hash_read_callback(song_hash):
    global current_playing, settings # current_playback_process is managed by play_audio and _perform_stop_playback

    song_filename = hash_map.get(song_hash)
    if (song_filename):
        # Stop any currently playing song and clear related status.
        # This is important to correctly terminate any existing loops or single plays.
        _perform_stop_playback()

        # Set current_playing for the new song *after* stopping the old one.
        current_playing = song_filename
        print(f"NFC: Matched {song_hash} to {song_filename}. Current_playing set to: {current_playing}")
        song_path = os.path.join(MUSIC_DIR, song_filename)

        if settings.get("loop_nfc_song"):
            print(f"NFC: Loop enabled for {song_filename}. Starting loop manager.")
            # The loop manager will handle the initial play and subsequent loops.
            loop_manager_thread = threading.Thread(target=nfc_song_loop_manager,
                                                 args=(song_path, song_filename, song_hash),
                                                 daemon=True)
            loop_manager_thread.start()
        else:
            # Play once if looping is not enabled.
            print(f"NFC: Loop disabled for {song_filename}. Playing once.")
            single_play_thread = threading.Thread(target=play_audio, args=(song_path,), daemon=True)
            single_play_thread.start()
    else:
        print(f"NFC: Hash {song_hash} not found in hash_map.")

def on_uid_read_callback(uid):
    # Called when a tag is detected (do nothing here)
    pass

def _perform_stop_playback():
    global current_playback_process, current_playing
    with playback_lock:
        if current_playback_process and current_playback_process.poll() is None:
            try:
                current_playback_process.terminate()
                current_playback_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                current_playback_process.kill()
                current_playback_process.wait()
            except Exception as e:
                print(f"Error stopping playback: {e}")
            current_playback_process = None
        current_playing = None # Ensure current_playing is cleared

@app.route('/stop')
def stop_playback():
    global current_playing
    _perform_stop_playback()
    current_playing = None
    return redirect(url_for('index'))

def on_tag_removed_callback():
    print("NFC tag removed, stopping playback.")
    _perform_stop_playback()

# Initialize NFC controller
nfc_controller = NFCController(
    serial_port="/dev/ttyS0",
    baud_rate=115200,
    debug_pn532=False,
    on_hash_read_callback=on_hash_read_callback,
    on_uid_read_callback=on_uid_read_callback,
    on_tag_removed_callback=on_tag_removed_callback
)

def connect_bluetooth_speaker():
    # Connect to the Bluetooth speaker using bluetoothctl
    try:
        subprocess.run([
            'bluetoothctl', 'connect', BLUETOOTH_SPEAKER_MAC
        ], check=True)
    except Exception as e:
        print(f"Bluetooth connection error: {e}")

def play_audio(filepath):
    global current_playback_process
    with playback_lock:
        # Stop any existing playback
        if current_playback_process and current_playback_process.poll() is None:
            try:
                current_playback_process.terminate()
                current_playback_process.wait(timeout=2)
            except Exception:
                pass
        # Start new playback
        current_playback_process = subprocess.Popen([
            'ffplay', '-nodisp', '-autoexit', '-loglevel', 'quiet', filepath
        ])

def load_auto_connect_devices():
    if os.path.exists(AUTO_CONNECT_FILE):
        with open(AUTO_CONNECT_FILE, 'r') as f:
            return json.load(f)
    return []

def save_auto_connect_devices(devices):
    with open(AUTO_CONNECT_FILE, 'w') as f:
        json.dump(devices, f)

def get_bluetooth_status():
    try:
        result = subprocess.run(['bluetoothctl', 'info', BLUETOOTH_SPEAKER_MAC], capture_output=True, text=True)
        if 'Connected: yes' in result.stdout:
            return 'Connected'
        elif 'Paired: yes' in result.stdout:
            return 'Paired, not connected'
        else:
            return 'Not connected'
    except Exception as e:
        return f'Error: {e}'

def save_current_bluetooth_device(mac):
    with open(CURRENT_DEVICE_FILE, 'w') as f:
        json.dump({'mac': mac}, f)

def load_current_bluetooth_device():
    if os.path.exists(CURRENT_DEVICE_FILE):
        with open(CURRENT_DEVICE_FILE, 'r') as f:
            data = json.load(f)
            return data.get('mac')
    return None

def load_nfc_map():
    if os.path.exists(NFC_MAP_FILE):
        with open(NFC_MAP_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_nfc_map(nfc_map):
    with open(NFC_MAP_FILE, 'w') as f:
        json.dump(nfc_map, f)

def build_hash_map():
    global hash_map
    # Load existing hash map if present
    if os.path.exists(HASH_MAP_FILE):
        with open(HASH_MAP_FILE, 'r') as f:
            hash_map = json.load(f)
    else:
        hash_map = {}
    # Build reverse map: filename -> hash
    file_to_hash = {v: k for k, v in hash_map.items()}
    # Get current set of mp3 files
    if not os.path.exists(MUSIC_DIR):
        return
    mp3_files = [f for f in os.listdir(MUSIC_DIR) if f.endswith('.mp3')]
    mp3_set = set(mp3_files)
    # Remove deleted files from hash_map
    removed = [fname for fname in file_to_hash if fname not in mp3_set]
    for fname in removed:
        h = file_to_hash[fname]
        del hash_map[h]
    # Add new files
    new_files = [f for f in mp3_files if f not in file_to_hash]
    total = len(new_files)
    if total > 0:
        print(f"Hashing {total} new MP3 files...")
    for idx, f in enumerate(new_files, 1):
        path = os.path.join(MUSIC_DIR, f)
        song_hash = compute_mp3_hash(path)
        hash_map[song_hash] = f
        # Print progress bar
        bar_len = 40
        filled_len = int(bar_len * idx // total) if total else 0
        bar = '=' * filled_len + '-' * (bar_len - filled_len)
        print(f"\r[{bar}] {idx}/{total} {f}", end='')
    if total > 0:
        print("\nHashing complete.")
    # Save to file for persistence
    with open(HASH_MAP_FILE, 'w') as out:
        json.dump(hash_map, out)

def load_hash_map():
    global hash_map
    if os.path.exists(HASH_MAP_FILE):
        with open(HASH_MAP_FILE, 'r') as f:
            hash_map = json.load(f)
    else:
        build_hash_map()

def compute_mp3_hash(filepath):
    """Compute SHA256 hash of an MP3 file."""
    hash_sha256 = hashlib.sha256()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_sha256.update(chunk)
    return hash_sha256.hexdigest()

@app.route('/')
def index():
    music_files = [f for f in os.listdir(MUSIC_DIR) if f.endswith('.mp3')]
    bluetooth_status = get_bluetooth_status()
    return render_template('index.html', music_files=music_files, bluetooth_status=bluetooth_status, current_playing=current_playing)

@app.route('/play/<filename>')
def play(filename):
    global current_playing
    filepath = os.path.join(MUSIC_DIR, filename)
    if not os.path.exists(filepath):
        return "File not found", 404
    connect_bluetooth_speaker()
    threading.Thread(target=play_audio, args=(filepath,), daemon=True).start()
    current_playing = filename
    return redirect(url_for('index'))

@app.route('/stop')
def web_stop_playback():
    global current_playback_process
    with playback_lock:
        if current_playback_process and current_playback_process.poll() is None:
            try:
                current_playback_process.terminate()
                current_playback_process.wait(timeout=2)
            except Exception:
                pass
            current_playback_process = None
    return redirect(url_for('index'))

@app.route('/bluetooth')
def bluetooth_panel():
    auto_connect = load_auto_connect_devices()
    return render_template('bluetooth.html', auto_connect=auto_connect)

@app.route('/bluetooth/scan')
def bluetooth_scan():
    try:
        # Start scanning
        scan_proc = subprocess.Popen(['bluetoothctl', 'scan', 'on'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(5)  # Scan for 5 seconds
        # Get list of discovered devices
        result = subprocess.run(['bluetoothctl', 'devices'], capture_output=True, text=True)
        devices = []
        for line in result.stdout.splitlines():
            if line.startswith('Device'):
                parts = line.split(' ', 2)
                if len(parts) == 3:
                    devices.append({'mac': parts[1], 'name': parts[2]})
        # Stop scanning, ignore errors
        try:
            subprocess.run(['bluetoothctl', 'scan', 'off'], capture_output=True, text=True)
        except Exception:
            pass
        scan_proc.terminate()
        return jsonify({'devices': devices})
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/bluetooth/connect/<mac>')
def bluetooth_connect(mac):
    try:
        # Check if already paired
        info = subprocess.run(['bluetoothctl', 'info', mac], capture_output=True, text=True)
        if 'Paired: yes' not in info.stdout:
            pair = subprocess.run(['bluetoothctl', 'pair', mac], capture_output=True, text=True)
            if pair.returncode != 0:
                return f"Error pairing: {pair.stdout or pair.stderr}", 500
        connect = subprocess.run(['bluetoothctl', 'connect', mac], capture_output=True, text=True)
        if connect.returncode != 0:
            return f"Error connecting: {connect.stdout or connect.stderr}", 500
        return redirect(url_for('bluetooth_panel'))
    except Exception as e:
        return f"Error connecting: {e}", 500

@app.route('/bluetooth/disconnect/<mac>')
def bluetooth_disconnect(mac):
    try:
        subprocess.run(['bluetoothctl', 'disconnect', mac], check=True)
        return redirect(url_for('bluetooth_panel'))
    except Exception as e:
        return f"Error disconnecting: {e}", 500

@app.route('/bluetooth/pair/<mac>')
def bluetooth_pair(mac):
    try:
        subprocess.run(['bluetoothctl', 'pair', mac], check=True)
        return redirect(url_for('bluetooth_panel'))
    except Exception as e:
        return f"Error pairing: {e}", 500

@app.route('/bluetooth/autoconnect/<mac>', methods=['POST'])
def bluetooth_autoconnect(mac):
    auto_connect = load_auto_connect_devices()
    if mac not in auto_connect:
        auto_connect.append(mac)
        save_auto_connect_devices(auto_connect)
    return '', 204

@app.route('/bluetooth/autoconnect/<mac>', methods=['DELETE'])
def bluetooth_remove_autoconnect(mac):
    auto_connect = load_auto_connect_devices()
    if mac in auto_connect:
        auto_connect.remove(mac)
        save_auto_connect_devices(auto_connect)
    return '', 204

@app.route('/bluetooth/info/<mac>')
def bluetooth_info(mac):
    try:
        result = subprocess.run(['bluetoothctl', 'info', mac], capture_output=True, text=True)
        return f'<pre>{result.stdout}</pre>'
    except Exception as e:
        return f'Error: {e}', 500

@app.route('/bluetooth/trust/<mac>')
def bluetooth_trust(mac):
    try:
        result = subprocess.run(['bluetoothctl', 'trust', mac], capture_output=True, text=True)
        if result.returncode == 0:
            return redirect(url_for('bluetooth_panel'))
        else:
            return f"Error trusting: {result.stdout or result.stderr}", 500
    except Exception as e:
        return f'Error: {e}', 500

@app.route('/bluetooth/remove/<mac>')
def bluetooth_remove(mac):
    try:
        result = subprocess.run(['bluetoothctl', 'remove', mac], capture_output=True, text=True)
        if result.returncode == 0:
            return redirect(url_for('bluetooth_panel'))
        else:
            return f"Error removing: {result.stdout or result.stderr}", 500
    except Exception as e:
        return f'Error: {e}', 500

@app.route('/bluetooth/save_current/<mac>')
def bluetooth_save_current(mac):
    save_current_bluetooth_device(mac)
    return redirect(url_for('bluetooth_panel'))

@app.route('/bluetooth/connected')
def bluetooth_connected():
    try:
        result = subprocess.run(['bluetoothctl', 'devices', 'Connected'], capture_output=True, text=True)
        devices = []
        for line in result.stdout.splitlines():
            if line.startswith('Device'):
                parts = line.split(' ', 2)
                if len(parts) == 3:
                    devices.append({'mac': parts[1], 'name': parts[2]})
        return jsonify({'devices': devices})
    except Exception as e:
        return jsonify({'error': str(e), 'devices': []})

@app.route('/nfc/register', methods=['GET', 'POST'])
def nfc_register():
    nfc_map = load_nfc_map()
    message = None
    music_files = [f for f in os.listdir(MUSIC_DIR) if f.endswith('.mp3')]
    if request.method == 'POST':
        song = request.form['song']
        song_path = os.path.join(MUSIC_DIR, song)
        song_hash = compute_mp3_hash(song_path)
        if not nfc_controller.is_connected():
            nfc_controller.connect()
        success, msg_or_uid = nfc_controller.write_hash_to_ntag(song_hash)
        if success:
            nfc_map[msg_or_uid] = song
            save_nfc_map(nfc_map)
            message = f"Success! Tag {msg_or_uid} registered to {song}."
        else:
            message = f"NFC error: {msg_or_uid}"
    return render_template('nfc_register.html', nfc_map=nfc_map, music_files=music_files, message=message)

@app.route('/nfc/scan_uid')
def nfc_scan_uid():
    if not nfc_controller.is_connected():
        nfc_controller.connect()
    uid = nfc_controller.scan_tag_uid_once(timeout_seconds=5.0)
    if uid:
        return jsonify({'uid': uid})
    else:
        return jsonify({'uid': None})

@app.route('/nfc/register_tag/<song_hash_to_write>')
def register_nfc_tag_route(song_hash_to_write):
    # In a real app, song_hash_to_write would come from a form or DB
    # after computing hash for a selected song.
    if not nfc_controller.is_connected():
        return "NFC Controller not connected. Please try again shortly.", 503

    # Potentially pause polling if it interferes, though the lock should manage.
    # was_polling = nfc_controller._polling_thread and nfc_controller._polling_thread.is_alive()
    # if was_polling: nfc_controller.stop_polling() # Or a more graceful pause

    success, message_or_uid = nfc_controller.write_hash_to_ntag(song_hash_to_write)

    # if was_polling: nfc_controller.start_polling() # Resume polling

    if success:
        return f"Tag successfully written with UID: {message_or_uid}", 200
    else:
        return f"Failed to write tag: {message_or_uid}", 500

@app.route('/nfc/delete_mapping/<uid>', methods=['POST'])
def delete_nfc_mapping(uid):
    nfc_map = load_nfc_map()
    if uid in nfc_map:
        del nfc_map[uid]
        save_nfc_map(nfc_map)
        flash(f'Mapping for UID {uid} deleted successfully.', 'success')
    else:
        flash(f'UID {uid} not found in mappings.', 'danger')
    return redirect(url_for('nfc_register'))

@app.route('/nfc/delete_registration/<uid>', methods=['POST'])
def delete_registration(uid):
    nfc_map = load_nfc_map()
    if uid in nfc_map:
        del nfc_map[uid]
        save_nfc_map(nfc_map)
        flash(f'Registration for tag {uid} deleted successfully.', 'success')
    else:
        flash(f'Tag {uid} not found.', 'danger')
    return redirect(url_for('nfc_register'))

@app.route('/manage_music', methods=['GET', 'POST'])
def manage_music():
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file part', 'danger')
            return redirect(url_for('manage_music'))

        file = request.files['file']
        if file.filename == '':
            flash('No selected file', 'danger')
            return redirect(url_for('manage_music'))

        if file and file.filename.endswith('.mp3'):
            filepath = os.path.join(MUSIC_DIR, file.filename)
            file.save(filepath)
            flash(f'File {file.filename} uploaded successfully.', 'success')

            # Calculate hash for the new file and update the hash map
            song_hash = compute_mp3_hash(filepath)
            hash_map[song_hash] = file.filename
            with open(HASH_MAP_FILE, 'w') as out:
                json.dump(hash_map, out)
        else:
            flash('Invalid file type. Only MP3 files are allowed.', 'danger')

        return redirect(url_for('manage_music'))

    music_files = [f for f in os.listdir(MUSIC_DIR) if f.endswith('.mp3')]
    return render_template('manage_music.html', music_files=music_files)

@app.route('/delete_music/<filename>', methods=['POST'])
def delete_music(filename):
    filepath = os.path.join(MUSIC_DIR, filename)
    if os.path.exists(filepath):
        os.remove(filepath)
        flash(f'File {filename} deleted successfully.', 'success')
    else:
        flash(f'File {filename} not found.', 'danger')
    return redirect(url_for('manage_music'))

@app.route('/current_status')
def current_status():
    return jsonify({"current_playing": current_playing})

@app.route('/settings', methods=['GET', 'POST'])
def settings_panel():
    global settings
    if request.method == 'POST':
        settings["loop_nfc_song"] = request.form.get('loop_nfc_song') == 'on'
        settings["idle_mode"] = request.form.get('idle_mode', 'do_nothing')
        settings["select_songs"] = request.form.getlist('select_songs')

        save_settings()
        flash('Settings updated successfully.', 'success')
        return redirect(url_for('settings_panel'))

    music_files = []
    if os.path.exists(MUSIC_DIR):
        music_files = sorted([f for f in os.listdir(MUSIC_DIR) if f.endswith('.mp3')])

    if 'select_songs' not in settings: # Ensure key exists
        settings['select_songs'] = []

    return render_template('settings.html', settings=settings, music_files=music_files)

def handle_idle_mode():
    global current_playing, current_playback_process, settings, MUSIC_DIR

    if current_playing or (current_playback_process and current_playback_process.poll() is None):
        return # Active playback

    idle_mode_setting = settings.get("idle_mode", "do_nothing")
    if idle_mode_setting == "do_nothing":
        return

    # Conceptual: Check if an NFC tag is present. If so, not truly idle for music.
    # if nfc_controller and nfc_controller.is_any_tag_present(): # Requires this method in nfc_handler
    #     return

    if idle_mode_setting == "play_random":
        if not os.path.exists(MUSIC_DIR): return
        all_music_files = [f for f in os.listdir(MUSIC_DIR) if f.endswith('.mp3')]
        if all_music_files:
            song_to_play = random.choice(all_music_files)
            print(f"Idle mode: Playing random song - {song_to_play}")
            song_path = os.path.join(MUSIC_DIR, song_to_play)
            threading.Thread(target=play_audio, args=(song_path,), daemon=True).start()
            current_playing = song_to_play
    elif idle_mode_setting == "play_select":
        selected_songs = settings.get("select_songs", [])
        valid_selected_songs = [s for s in selected_songs if os.path.exists(os.path.join(MUSIC_DIR, s))]

        if set(selected_songs) != set(valid_selected_songs):
            settings["select_songs"] = valid_selected_songs
            save_settings() # Update stored settings if invalid songs were removed
            if not valid_selected_songs:
                 print("Idle mode: No valid songs selected or all selected songs are missing.")
                 return

        if valid_selected_songs:
            song_to_play = random.choice(valid_selected_songs)
            print(f"Idle mode: Playing selected song - {song_to_play}")
            song_path = os.path.join(MUSIC_DIR, song_to_play)
            threading.Thread(target=play_audio, args=(song_path,), daemon=True).start()
            current_playing = song_to_play
        else:
            print("Idle mode: No songs selected or available for 'play_select'.")

idle_thread = None
stop_idle_event = threading.Event()
last_idle_activity_time = time.time() # Tracks when a song started or NFC was active

def idle_mode_manager():
    print("Idle mode manager thread started.")
    global last_idle_activity_time

    while not stop_idle_event.is_set():
        time.sleep(2) # Check every 2 seconds

        # Conceptual: Update last_idle_activity_time if NFC tag is present
        # if nfc_controller and nfc_controller.is_any_tag_present():
        #     last_idle_activity_time = time.time()
        #     continue

        if current_playing or (current_playback_process and current_playback_process.poll() is None):
            last_idle_activity_time = time.time() # Active playback, reset timer
            continue

        if settings.get("idle_mode", "do_nothing") == "do_nothing":
            continue # Idle mode is off

        # Only trigger idle mode if inactive for a certain period (e.g., 30 seconds)
        if time.time() - last_idle_activity_time > 30:
            print("Device has been idle for >30s. Triggering idle mode.")
            handle_idle_mode()
            if current_playing: # If idle mode started a song
                last_idle_activity_time = time.time() # Reset timer after starting idle song
        else:
            print("Device is active or idle mode not triggered yet.")
    print("Idle mode manager thread stopped.")

# NEW function to manage NFC song looping
def nfc_song_loop_manager(song_path, song_filename, original_song_hash):
    global current_playing, current_playback_process, settings, playback_lock

    print(f"NFC Loop Mgr: Started for {song_filename} (Hash: {original_song_hash}).")

    iteration = 0
    while True:
        iteration += 1
        # print(f"NFC Loop Mgr ({song_filename}): Iteration {iteration}.") # Optional: for detailed logging

        # Check loop conditions *before* attempting to play
        if not settings.get("loop_nfc_song"):
            print(f"NFC Loop Mgr ({song_filename}): Loop setting now disabled. Exiting.")
            break

        if current_playing != song_filename:
            print(f"NFC Loop Mgr ({song_filename}): current_playing is now '{current_playing}'. Expected '{song_filename}'. Exiting.")
            break

        # Conceptual: Add a check here if the original NFC tag (original_song_hash) is still present
        # e.g., if nfc_controller.get_current_tag_hash() != original_song_hash: break

        if iteration > 1: # Only print "Looping" for subsequent plays
             print(f"NFC Loop Mgr: Looping song: {song_filename}")
        else:
            print(f"NFC Loop Mgr: Initial play for {song_filename} in loop.")

        play_audio(song_path) # This sets/updates current_playback_process

        process_to_wait_on = None
        # Safely get the reference to the Popen object
        with playback_lock: # Although play_audio uses this, good practice for reading shared resource
            process_to_wait_on = current_playback_process

        if process_to_wait_on:
            print(f"NFC Loop Mgr ({song_filename}): Waiting for playback to finish (PID: {process_to_wait_on.pid}).")
            try:
                process_to_wait_on.wait() # Block until ffplay process for this song instance exits
                print(f"NFC Loop Mgr ({song_filename}): Playback finished.")
            except Exception as e:
                print(f"NFC Loop Mgr ({song_filename}): Error waiting for playback: {e}. Exiting loop.")
                # Ensure current_playing is cleared if this loop was the source and an error occurred during wait
                if current_playing == song_filename:
                    _perform_stop_playback() # Stop and clear status
                break
        else:
            print(f"NFC Loop Mgr ({song_filename}): No playback process found after play_audio call. Exiting loop.")
            if current_playing == song_filename: # If this loop thought it was playing
                 _perform_stop_playback()
            break

        # Small delay to allow other threads to run and prevent extremely tight loops if audio is very short.
        time.sleep(0.1)

    print(f"NFC Loop Mgr: Exiting for {song_filename}.")

# --- Application Startup and Shutdown ---
def start_nfc_services():
    logger.info("Starting NFC services...")
    if nfc_controller.start_polling():
        logger.info("NFC polling thread started successfully.")
    else:
        logger.error(f"Failed to start NFC polling: {nfc_controller._last_error}")

def shutdown_nfc_services():
    logger.info("Shutting down NFC services...")
    nfc_controller.stop_polling()

if __name__ == '__main__':
    if not os.path.exists(MUSIC_DIR):
        os.makedirs(MUSIC_DIR)
    # Build hash map for all songs on startup
    build_hash_map()
    # Auto-connect to saved device on startup
    current_mac = load_current_bluetooth_device()
    if current_mac:
        try:
            subprocess.run(['bluetoothctl', 'connect', current_mac], check=True)
        except Exception as e:
            print(f"Bluetooth auto-connect error: {e}")
    # Start NFC services
    start_nfc_services()

    # Start idle mode manager thread if not "do_nothing"
    if settings.get("idle_mode", "do_nothing") != "do_nothing":
        stop_idle_event.clear()
        idle_thread = threading.Thread(target=idle_mode_manager, daemon=True)
        idle_thread.start()

    try:
        app.run(debug=True, host='0.0.0.0', port=5000, use_reloader=False)
    finally:
        print("Shutting down application...")
        shutdown_nfc_services()

        if idle_thread:
            print("Stopping idle mode manager...")
            stop_idle_event.set()
            idle_thread.join(timeout=5)

        # save_settings() is registered with atexit, so it will be called automatically.
        print("Application shutdown complete.")
