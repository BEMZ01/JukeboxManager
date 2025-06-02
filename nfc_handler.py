# nfc_handler.py
import time
import threading
import logging

# Attempt to import necessary libraries
try:
    import serial
    from adafruit_pn532.uart import PN532_UART
except ImportError:
    print("IMPORTANT: 'pyserial' or 'adafruit-circuitpython-pn532' library not found.")
    print("Please install them: pip install pyserial adafruit-circuitpython-pn532")


    # Define dummy classes if imports fail, so the main app can still import this module
    # and potentially run without NFC functionality if designed to do so.
    class PN532_UART:
        def __init__(self, *args, **kwargs):
            raise ImportError("adafruit-circuitpython-pn532 not installed")

        def SAM_configuration(self, *args, **kwargs): pass

        def read_passive_target(self, *args, **kwargs): return None

        def ntag2xx_read_block(self, *args, **kwargs): return None

        def ntag2xx_write_block(self, *args, **kwargs): pass

        @property
        def firmware_version(self): return (None, "N/A", "N/A", None)


    class Serial:
        def __init__(self, *args, **kwargs):
            raise ImportError("pyserial not installed")

        def open(self): pass

        def close(self): pass

        @property
        def is_open(self): return False

# Setup basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')
logger = logging.getLogger(__name__)

DEFAULT_SHA256_HASH_BYTES = 32  # SHA256 produces a 32-byte hash
DEFAULT_NTAG_BLOCK_SIZE = 4
DEFAULT_STARTING_BLOCK_NTAG = 4  # Common starting user block for NTAGs


class NFCController:
    def __init__(self, serial_port="/dev/ttyS0", baud_rate=115200, debug_pn532=False,
                 on_hash_read_callback=None, on_uid_read_callback=None, on_tag_removed_callback=None):
        """
        Initializes the NFCController.

        Args:
            serial_port (str): The serial port for the PN532 (e.g., "/dev/ttyS0", "/dev/serial0").
            baud_rate (int): The baud rate for serial communication.
            debug_pn532 (bool): Enable debug output from the PN532 library.
            on_hash_read_callback (callable): Callback function to be invoked when a hash is successfully read
                                             from a tag. Receives the hex string of the hash.
            on_uid_read_callback (callable): Callback function to be invoked when a UID is read by the
                                            polling thread (can be used for presence detection). Receives UID string.
            on_tag_removed_callback (callable): Callback function to be invoked when a tag is removed from the field.
        """
        self._serial_port_path = serial_port
        self._baud_rate = baud_rate
        self._debug_pn532 = debug_pn532
        self.on_hash_read_callback = on_hash_read_callback
        self.on_uid_read_callback = on_uid_read_callback
        self.on_tag_removed_callback = on_tag_removed_callback

        self._uart = None
        self._pn532 = None
        self._lock = threading.Lock()
        self._polling_thread = None
        self._stop_event = threading.Event()
        self._last_error = None
        self._is_connected_flag = False

    def _log_error(self, message):
        logger.error(message)
        self._last_error = message

    def _log_info(self, message):
        logger.info(message)

    def connect(self):
        """
        Establishes connection to the PN532 module.
        Returns True on success, False on failure.
        """
        if self._is_connected_flag:
            self._log_info("Already connected.")
            return True

        with self._lock:  # Ensure exclusive access during connection attempt
            try:
                self._log_info(f"Attempting to connect to PN532 on {self._serial_port_path} at {self._baud_rate} baud.")
                # Close if already open (e.g., from a failed previous attempt)
                if self._uart and self._uart.is_open:
                    self._uart.close()

                self._uart = serial.Serial(self._serial_port_path, baudrate=self._baud_rate, timeout=1)
                time.sleep(0.1)  # Short delay after opening port
                self._pn532 = PN532_UART(self._uart, debug=self._debug_pn532)

                ic, ver, rev, support = self._pn532.firmware_version
                self._log_info(f"Found PN532 with firmware version: {ver}.{rev} (IC: 0x{ic:X}, Support: 0x{support:X})")

                self._pn532.SAM_configuration()
                self._log_info("PN532 SAM configured.")
                self._is_connected_flag = True
                self._last_error = None
                return True
            except ImportError as e:  # Catch if libraries were not found at runtime
                self._log_error(f"Failed to initialize PN532: {e}. Required libraries might be missing.")
                self._is_connected_flag = False
                return False
            except Exception as e:
                self._log_error(f"Failed to initialize PN532: {e}")
                self._is_connected_flag = False
                if self._uart and self._uart.is_open:
                    self._uart.close()
                return False

    def is_connected(self):
        return self._is_connected_flag and self._pn532 is not None and self._uart is not None and self._uart.is_open

    def _polling_loop(self):
        self._log_info("NFC polling thread started.")
        last_uid_str = None
        tag_present_continuously = False

        while not self._stop_event.is_set():
            if not self.is_connected():
                self._log_error("NFC disconnected in polling loop. Attempting to reconnect...")
                if not self.connect():  # Attempt to reconnect
                    self._log_error("Reconnect failed. Waiting before retrying.")
                    time.sleep(5)  # Wait before next reconnect attempt
                    continue
                else:
                    self._log_info("Successfully reconnected.")

            uid_bytes = None
            current_hash_hex = None

            try:
                with self._lock:
                    # Check connection again inside lock before operation
                    if not self.is_connected():
                        raise RuntimeError("PN532 not connected or UART port closed.")
                    uid_bytes = self._pn532.read_passive_target(timeout=0.5)  # ms timeout

                if uid_bytes:
                    uid_str = ''.join([f'{b:02X}' for b in uid_bytes])
                    if self.on_uid_read_callback:
                        try:
                            self.on_uid_read_callback(uid_str)
                        except Exception as cb_e:
                            self._log_error(f"Error in on_uid_read_callback: {cb_e}")

                    if uid_str != last_uid_str:  # New tag presentation
                        self._log_info(f"New tag detected: {uid_str}")
                        last_uid_str = uid_str
                        tag_present_continuously = True  # Reset continuous presence flag

                        # Attempt to read hash (assuming NTAG2xx for now)
                        tag_data_bytes = bytearray()
                        read_success = True
                        with self._lock:  # Lock for multi-block read sequence
                            for block_num in range(DEFAULT_STARTING_BLOCK_NTAG, DEFAULT_STARTING_BLOCK_NTAG + (
                                    DEFAULT_SHA256_HASH_BYTES // DEFAULT_NTAG_BLOCK_SIZE)):
                                block_data = self._pn532.ntag2xx_read_block(block_num)
                                if block_data:
                                    tag_data_bytes.extend(block_data)
                                else:
                                    self._log_error(f"Failed to read block {block_num} from tag {uid_str}.")
                                    read_success = False
                                    break

                        if read_success and len(tag_data_bytes) >= DEFAULT_SHA256_HASH_BYTES:
                            current_hash_hex = tag_data_bytes[:DEFAULT_SHA256_HASH_BYTES].hex().lower()
                            self._log_info(f"Successfully read hash from tag {uid_str}: {current_hash_hex}")
                            if self.on_hash_read_callback:
                                try:
                                    self.on_hash_read_callback(current_hash_hex)
                                except Exception as cb_e:
                                    self._log_error(f"Error in on_hash_read_callback: {cb_e}")
                        elif read_success:
                            self._log_info(f"Incomplete hash read from tag {uid_str}. Got {len(tag_data_bytes)} bytes.")
                        # If not a new tag but still present, do nothing to avoid re-triggering hash read/callback

                else:  # No tag detected
                    if tag_present_continuously:  # Tag was just removed
                        self._log_info("Tag removed from field.")
                        last_uid_str = None
                        tag_present_continuously = False
                        if self.on_tag_removed_callback:
                            try:
                                self.on_tag_removed_callback()
                            except Exception as cb_e:
                                self._log_error(f"Error in on_tag_removed_callback: {cb_e}")

                time.sleep(0.1)  # Polling interval outside of read_passive_target timeout

            except RuntimeError as e:
                self._log_error(f"RuntimeError in polling loop: {e}. PN532 might have issues.")
                # This might indicate a need to reset the connection
                self._is_connected_flag = False  # Assume connection is lost
                time.sleep(2)  # Wait a bit before trying to reconnect in the next loop iteration
            except Exception as e:
                self._log_error(f"Unexpected error in polling loop: {e}")
                time.sleep(1)

        self._log_info("NFC polling thread stopped.")

    def start_polling(self):
        """Starts the NFC polling thread."""
        if self._polling_thread and self._polling_thread.is_alive():
            self._log_info("Polling thread already running.")
            return True

        if not self.is_connected():
            if not self.connect():
                self._log_error("Cannot start polling: Connection to PN532 failed.")
                return False

        self._stop_event.clear()
        self._polling_thread = threading.Thread(target=self._polling_loop, daemon=True)
        self._polling_thread.start()
        return True

    def stop_polling(self):
        """Stops the NFC polling thread and closes resources."""
        self._log_info("Attempting to stop NFC polling thread...")
        self._stop_event.set()
        if self._polling_thread and self._polling_thread.is_alive():
            self._polling_thread.join(timeout=5.0)  # Wait for thread to finish
            if self._polling_thread.is_alive():
                self._log_error("Polling thread did not terminate gracefully.")
        self._polling_thread = None

        with self._lock:  # Ensure exclusive access for closing
            if self._uart and self._uart.is_open:
                self._log_info(f"Closing serial port {self._serial_port_path}.")
                self._uart.close()
            self._pn532 = None
            self._uart = None
            self._is_connected_flag = False
        self._log_info("NFC resources released.")

    def scan_tag_uid_once(self, timeout_seconds=5.0):
        """
        Scans for a single NFC tag and returns its UID.
        This is a blocking call.

        Args:
            timeout_seconds (float): How long to wait for a tag.

        Returns:
            str: The UID of the tag as a hex string, or None if no tag is found or an error occurs.
        """
        if not self.is_connected():
            self._log_error("Cannot scan UID: Not connected to PN532.")
            if not self.connect():  # Try to connect if not connected
                self._log_error("Failed to connect for UID scan.")
                return None

        uid_bytes = None
        try:
            with self._lock:
                if not self.is_connected():
                    raise RuntimeError("PN532 not connected or UART port closed before UID scan.")
                self._log_info(f"Scanning for tag UID (timeout: {timeout_seconds}s)...")
                uid_bytes = self._pn532.read_passive_target(timeout=timeout_seconds * 1000)  # Library expects ms

            if uid_bytes:
                uid_str = ''.join([f'{b:02X}' for b in uid_bytes])
                self._log_info(f"Tag found with UID: {uid_str}")
                return uid_str
            else:
                self._log_info("No tag found within timeout for UID scan.")
                return None
        except Exception as e:
            self._log_error(f"Error during single UID scan: {e}")
            return None

    def write_hash_to_ntag(self, song_hash_hex, wait_for_tag_timeout=10.0):
        """
        Waits for an NTAG2xx tag and writes the provided song hash to it.
        The hash should be a 64-character hex string (for a 32-byte hash).
        Writes to blocks 4-11 by default.

        Args:
            song_hash_hex (str): The SHA256 hash as a 64-character hex string.
            wait_for_tag_timeout (float): Max seconds to wait for a tag to be presented.

        Returns:
            tuple: (bool: success, str: message_or_uid)
                   If success is True, message_or_uid is the UID of the written tag.
                   If success is False, message_or_uid is an error message.
        """
        if not (isinstance(song_hash_hex, str) and len(song_hash_hex) == DEFAULT_SHA256_HASH_BYTES * 2):
            msg = f"Invalid song_hash_hex format. Expected a {DEFAULT_SHA256_HASH_BYTES * 2}-character hex string."
            self._log_error(msg)
            return False, msg

        if not self.is_connected():
            self._log_error("Cannot write to tag: Not connected to PN532.")
            if not self.connect():
                self._log_error("Failed to connect for tag write.")
                return False, "Failed to connect to PN532 for writing."

        try:
            hash_bytes = bytes.fromhex(song_hash_hex)
        except ValueError:
            msg = "Invalid hex string for song_hash_hex."
            self._log_error(msg)
            return False, msg

        self._log_info(f"Please present an NTAG tag to write hash: {song_hash_hex[:8]}...")

        uid_bytes = None
        start_time = time.monotonic()
        while time.monotonic() - start_time < wait_for_tag_timeout:
            with self._lock:
                if not self.is_connected():
                    raise RuntimeError("PN532 not connected or UART port closed before tag write attempt.")
                uid_bytes_temp = self._pn532.read_passive_target(timeout=100)  # Short poll, 100ms
            if uid_bytes_temp:
                uid_bytes = uid_bytes_temp
                break
            time.sleep(0.1)  # Brief pause before retrying read_passive_target

        if not uid_bytes:
            msg = "No tag presented for writing within timeout."
            self._log_info(msg)
            return False, msg

        uid_str = ''.join([f'{b:02X}' for b in uid_bytes])
        self._log_info(f"Tag {uid_str} detected. Attempting to write hash...")

        try:
            with self._lock:  # Lock for the entire write sequence
                if not self.is_connected():
                    raise RuntimeError("PN532 not connected or UART port closed before actual write blocks.")
                for i in range(DEFAULT_SHA256_HASH_BYTES // DEFAULT_NTAG_BLOCK_SIZE):
                    block_number = DEFAULT_STARTING_BLOCK_NTAG + i
                    data_chunk = hash_bytes[i * DEFAULT_NTAG_BLOCK_SIZE: (i + 1) * DEFAULT_NTAG_BLOCK_SIZE]
                    self._log_info(f"Writing to block {block_number} on tag {uid_str} with data: {data_chunk.hex()}")
                    self._pn532.ntag2xx_write_block(block_number, data_chunk)

            self._log_info(f"Successfully wrote hash to tag {uid_str}.")
            return True, uid_str
        except Exception as e:
            msg = f"Error writing hash to tag {uid_str}: {e}"
            self._log_error(msg)
            return False, msg

    def get_last_error(self):
        """Returns the last recorded error message."""
        return self._last_error


# Example of how to use this module (for testing within this file if run directly)
if __name__ == '__main__':
    print("NFC Controller Module - Self-Test")


    # Define a simple callback for testing
    def my_hash_callback(song_hash):
        print(f"CALLBACK: Song Hash Read = {song_hash}")


    def my_uid_callback(uid_str):
        print(f"CALLBACK: Tag UID Detected = {uid_str}")


    # Adjust serial port if different, e.g., for Windows/macOS
    # serial_port = "COM3" # Example for Windows
    # serial_port = "/dev/tty.usbserial-xxxx" # Example for macOS
    serial_port = "/dev/ttyS0"  # For Raspberry Pi, or use "/dev/serial0"

    nfc = NFCController(serial_port=serial_port,
                        on_hash_read_callback=my_hash_callback,
                        on_uid_read_callback=my_uid_callback,
                        debug_pn532=False)  # Set debug_pn532=True for verbose PN532 lib output

    if nfc.start_polling():
        print("NFC polling started. Try placing an NTAG2xx tag near the reader.")
        print("The tag should have a hash written to blocks 4-11 for the hash callback to trigger.")
        print("To test writing, call a write function (not implemented in this self-test loop).")
        try:
            # Keep the main thread alive to let the polling thread run
            # Or integrate into your application's main loop / event system
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("Keyboard interrupt received.")
        finally:
            print("Stopping NFC polling...")
            nfc.stop_polling()
            print("NFC Controller stopped.")
    else:
        print(f"Failed to start NFC Controller. Last error: {nfc.get_last_error()}")

    # Example of writing (run this part separately or after stopping polling,
    # as polling might interfere with focused write operations if not handled carefully,
    # though the lock should help).
    # print("\n--- Test Writing ---")
    # if nfc.connect(): # Ensure connected before writing
    #     # Dummy hash for testing - replace with a real song hash
    #     sample_hash = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
    #     print(f"Attempting to write sample hash: {sample_hash}")
    #     success, message = nfc.write_hash_to_ntag(sample_hash)
    #     if success:
    #         print(f"Write successful to UID: {message}")
    #     else:
    #         print(f"Write failed: {message}")
    #     nfc.stop_polling() # Clean up connection if only testing write
    # else:
    #     print("Could not connect to NFC reader for write test.")

