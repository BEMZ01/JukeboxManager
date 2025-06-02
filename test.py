import serial
import adafruit_pn532.uart
import time # Good for adding delays if needed for testing

# SERIAL_PORT = "/dev/serial0"
SERIAL_PORT = "/dev/ttyS0" # Your original

print(f"Attempting to connect to PN532 on {SERIAL_PORT}...")

try:
    # Ensure the port isn't busy from a previous run if the script crashed
    # This is a bit of a hack, but can help in some test scenarios
    try:
        temp_uart = serial.Serial(SERIAL_PORT, baudrate=115200, timeout=0.05)
        temp_uart.close()
        print("Closed pre-existing serial port connection if any.")
    except serial.SerialException:
        pass # Port wasn't open, that's fine.

    uart = serial.Serial(SERIAL_PORT, baudrate=115200, timeout=1)
    # Give the UART and PN532 a moment, especially after opening the port
    time.sleep(0.1) # Optional short delay

    # The PN532_UART constructor itself calls firmware_version
    pn532 = adafruit_pn532.uart.PN532_UART(uart, debug=True) # debug=True might give more low-level info

    # If the constructor succeeds, firmware_version was already called.
    # You can call it again explicitly if you want to re-verify or see the output directly here.
    ic, ver, rev, support = pn532.firmware_version
    print(f"Found PN532 with firmware version: {ver}.{rev}")
    print(f"IC: {ic}, Support: {support}")

except serial.SerialException as se:
    print(f"SerialException: Could not open port {SERIAL_PORT}.")
    print(f"Details: {se}")
    print("Check if the port exists, is not in use by another process (like serial console), and you have permissions (e.g., member of 'dialout' group).")
except RuntimeError as re:
    print(f"RuntimeError: Failed to detect the PN532. Details: {re}")
    print("This usually means the board isn't responding. Check:")
    print("  1. Wiring (TX-RX swapped? GND connected? VCC connected?).")
    print("  2. PN532 module is set to UART mode (check jumpers).")
    print("  3. Raspberry Pi serial console is DISABLED on this UART port.")
    print("  4. Baudrate (115200 is standard for this library).")
except Exception as e:
    print(f"An unexpected error occurred: {e}")
finally:
    if 'uart' in locals() and uart.is_open:
        uart.close()
        print(f"Closed serial port {SERIAL_PORT}.")