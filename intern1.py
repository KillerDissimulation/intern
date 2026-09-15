import serial
import time

# =========================
# CONFIG
# =========================

PORT = "/dev/ttyUSB0"
BAUD = 115200

# Known working M328GW commands
# Gateway address = 1

START_SCAN = bytes.fromhex(
    "7E 55 09 00 00 01 00 40 00 00 42 22"
)

GET_RESULT = bytes.fromhex(
    "7E 55 08 00 00 01 00 41 00 99 11"
)


# =========================
# SERIAL
# =========================

ser = serial.Serial(
    PORT,
    BAUD,
    timeout=0.5
)

time.sleep(1)

print("====================================")
print(" M328 - CONTINUOUS 9 READER SCANNER")
print("====================================")
print("Port    :", PORT)
print("Baud    :", BAUD)
print("Gateway : 1")
print()
print("Scanning Readers 1-9...")
print("Press Ctrl+C to stop.")
print()


def receive_response():
    time.sleep(0.1)
    return ser.read(512)


def scan_all_readers():

    # Clear old serial data
    ser.reset_input_buffer()

    # -------------------------
    # 1. START SCAN
    # -------------------------

    ser.write(START_SCAN)

    start_response = receive_response()

    if not start_response:
        print("[ERROR] No START_SCAN response")
        return {}

    # -------------------------
    # 2. GET RESULTS
    # -------------------------

    time.sleep(0.1)

    ser.write(GET_RESULT)

    response = receive_response()

    if not response:
        print("[ERROR] No GET_RESULT response")
        return {}

    if len(response) < 13:
        print(
            "[ERROR] Response too short:",
            response.hex(" ").upper()
        )
        return {}

    # -------------------------
    # PARSE PAYLOAD
    # -------------------------

    payload_length = (
        response[10] |
        (response[11] << 8)
    )

    payload = response[
        12:12 + payload_length
    ]

    if len(payload) < 1:
        return {}

    reader_count = payload[0]

    if reader_count == 0:
        return {}

    record_size = (
        len(payload) - 1
    ) // reader_count

    cards = {}

    for i in range(reader_count):

        start = 1 + (i * record_size)

        record = payload[
            start:start + record_size
        ]

        if len(record) < 8:
            continue

        uid_raw = record[:8]

        # 0000000000000000 means no tag
        if all(byte == 0 for byte in uid_raw):
            continue

        # Manufacturer program reverses UID for display
        uid = uid_raw[::-1].hex().upper()

        reader_number = i + 1

        cards[reader_number] = uid

    return cards


# =========================
# MAIN LOOP
# =========================

try:

    while True:

        cards = scan_all_readers()

        if cards:

            for reader, uid in cards.items():

                print(
                    f"Reader {reader} -> {uid}"
                )

        time.sleep(0.25)


except KeyboardInterrupt:

    print()
    print("Scanner stopped.")


finally:

    ser.close()
